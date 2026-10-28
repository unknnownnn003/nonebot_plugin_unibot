"""The single guessing-game state machine shared by commands and Agent tools."""
from __future__ import annotations
import asyncio
import io
import time
from uuid import uuid4
from .results import outcome, image


def _data(g, key):
    from .. import chuguess as c
    hints = g.get("hints_used", set())
    elapsed = max(0, int(time.time() - float(g.get("start_time", time.time()))))
    return dict(group_id=int(key), active=True, difficulty=g["difficulty"],
                score=c.calc_actual_score(g["score"], hints), base_score=g["score"],
                hints_used=len(hints), elapsed_seconds=elapsed, remaining_seconds=max(0, c.GAME_TIMEOUT - elapsed),
                hints_remaining=sum(h not in hints or h in ("name_part", "more_area") for h in c.HINT_TYPES))


async def status(group_id):
    from .. import chuguess as c
    key = str(group_id)
    async with await c._get_group_lock(key):
        game = c.active_games.get(key)
        if not game:
            return outcome("NO_ACTIVE_GAME", "当前没有进行中的猜曲游戏。", status="needs_input")
        return outcome(data=_data(game, key))


async def binding(group_id):
    from .. import chuguess as c
    async with await c._get_group_lock(str(group_id)):
        game = c.active_games.get(str(group_id))
        if game is None:
            return None
        return game.setdefault("round_id", uuid4().hex)


def _matches(game, expected_round):
    return expected_round is None or game.get("round_id") == expected_round


def _consume(c, key, game):
    if c.active_games.get(key) is not game:
        return False
    c.active_games.pop(key)
    task = c.game_tasks.pop(key, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()
    return True


async def _result_image(c, game, score, **kwargs):
    return await asyncio.to_thread(c.make_game_result_image, game["song"], game["jacket"],
        game["crop_x"], game["crop_y"], game["crop_w"], game["crop_h"], game["difficulty"], score, **kwargs)


async def hint(group_id, *, expected_round=None):
    from .. import chuguess as c
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    key = str(group_id)
    async with await c._get_group_lock(key):
        game = c.active_games.get(key)
        if not game:
            return outcome("NO_ACTIVE_GAME", "当前没有进行中的猜曲游戏。使用 /chuguess 开始一局！", status="needs_input")
        if not _matches(game, expected_round):
            return outcome("STATE_CHANGED", "原猜曲局已结束或改变，请重新提出请求。", status="needs_input")
        value = await c.get_hint_text(game)
        if value is None:
            return outcome("NO_HINT", "获取提示失败，请稍后再试。", status="unavailable")
        kind, content = value
        result = outcome(data={"hint_type": kind})
        if kind == "text":
            result["data"]["hint"] = str(content)
        elif kind in ("location", "more_area"):
            label = "[提示] 截取位置:" if kind == "location" else "[提示] 额外曲绘区域:"
            result["artifacts"] = [dict(kind="message", content=Message([MessageSegment.text(label), content]))]
        else:
            return outcome("NO_HINT", "获取提示失败，请稍后再试。", status="unavailable")
        result.update(effect="local_write", side_effect_state="committed")
        return result


async def give_up(group_id, *, expected=None, reason="giveup", expected_round=None):
    from .. import chuguess as c
    key = str(group_id)
    async with await c._get_group_lock(key):
        game = c.active_games.get(key)
        if not game or (expected is not None and game is not expected):
            return outcome("NO_ACTIVE_GAME", "当前没有进行中的猜曲游戏。", status="needs_input")
        if not _matches(game, expected_round):
            return outcome("STATE_CHANGED", "原猜曲局已结束或改变，请重新提出请求。", status="needs_input")
        content = await _result_image(c, game, game["score"], guessed=False, end_reason=reason)
        if not _consume(c, key, game):
            return outcome("NO_ACTIVE_GAME", "当前游戏已经结束。", status="needs_input")
        result = outcome(data={"group_id": int(group_id), "title": game["song"].get("title", "Unknown"),
                               "difficulty": game["difficulty"]}, artifacts=[image(content)])
        result.update(effect="local_write", side_effect_state="committed")
        return result


async def start(group_id, difficulty="mas", *, bot_id=None):
    from .. import chuguess as c
    key = str(group_id)
    async with await c._get_group_lock(key):
        if key in c.active_games:
            return outcome("ALREADY_ACTIVE", "当前群已有进行中的猜曲游戏。", status="needs_input")
        if difficulty not in c.DIFFICULTY_CONFIG:
            return outcome("INVALID_ARGUMENT", "难度必须是bas、adv、exp、mas或ult。", status="needs_input")
        picked = await asyncio.to_thread(c.pick_random_song_and_jacket)
        if not picked:
            return outcome("NO_SONG", "未能加载曲绘，请检查曲库和曲绘数据。", status="unavailable")
        song, jacket, _ = picked
        config = c.DIFFICULTY_CONFIG[difficulty]
        def crop():
            cropped, x, y, w, h = c.crop_jacket(jacket, config["ratio"])
            buffer = io.BytesIO()
            converted = cropped.convert("RGB")
            try:
                converted.save(buffer, format="JPEG", quality=90)
                return buffer.getvalue(), x, y, w, h
            finally:
                converted.close()
                cropped.close()
        content, x, y, w, h = await asyncio.to_thread(crop)
        game = dict(song=song, jacket=jacket, crop_x=x, crop_y=y, crop_w=w, crop_h=h,
                    difficulty=difficulty, score=config["score"], start_time=time.time(), hints_used=set(),
                    name_reveal_step=0, revealed_areas=[(x,y,w,h)], group_id=key, bot_id=bot_id, round_id=uuid4().hex)
        c.active_games[key] = game
        c.game_tasks[key] = asyncio.create_task(c.game_timeout(key, game))
        result = outcome(data=_data(game, key), artifacts=[image(content)])
        result.update(effect="local_write", side_effect_state="committed")
        return result


async def guess(group_id, query, user_id, *, guesser_name=None, expected_round=None):
    from .. import chuguess as c
    key = str(group_id)
    async with await c._get_group_lock(key):
        game = c.active_games.get(key)
        if not game:
            return outcome("NO_ACTIVE_GAME", "当前没有进行中的猜曲游戏。", status="needs_input")
        if not _matches(game, expected_round):
            return outcome("STATE_CHANGED", "原猜曲局已结束或改变，请重新提出请求。", status="needs_input")
        matched = await asyncio.to_thread(c.match_song_by_query, query)
        if not matched:
            return outcome("NOT_FOUND", "没有匹配到歌曲，再猜猜看？", status="needs_input")
        if str(matched.get("id")) != str(game["song"].get("id")):
            return outcome("WRONG_GUESS", "不对哦，再猜猜看～", status="needs_input")
        score = c.calc_actual_score(game["score"], game["hints_used"])
        content = await _result_image(c, game, score, guessed=True, guesser_name=guesser_name or str(user_id))
        def add_score():
            scores = c.load_scores()
            scores[str(user_id)] = scores.get(str(user_id), 0) + score
            c.save_scores(scores)
            return scores[str(user_id)]
        try:
            async with c._score_lock:
                # Observe the thread's write even if the outer request is cancelled.
                commit = asyncio.create_task(asyncio.to_thread(add_score))
                try:
                    total = await asyncio.shield(commit)
                except asyncio.CancelledError:
                    total = await commit
        except Exception:
            _consume(c, key, game)
            result = outcome("RESULT_UNKNOWN", "游戏已结束，但记分结果需要管理员核对。", status="unknown")
            result.update(effect="local_write", side_effect_state="unknown")
            return result
        _consume(c, key, game)
        result = outcome(data={"group_id": int(group_id), "title": game["song"].get("title", ""),
                               "score": score, "total_score": total, "base_score": game["score"],
                               "hint_count": len(game["hints_used"] & c.SCORE_HINT_TYPES)}, artifacts=[image(content)])
        result.update(effect="local_write", side_effect_state="committed")
        return result
