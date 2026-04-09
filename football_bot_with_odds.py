#!/usr/bin/env python3
"""
Football Bot with Odds API - 足球機器人 + 博彩賠率
每天台灣時間 14:00 發送：昨日賽果 + 今日預測 + 賭盤賠率
支持聯賽：英超、西甲、意甲、德甲、法甲
"""

import os
import asyncio
import logging
import requests
from datetime import datetime, timedelta, timezone
from telegram import Bot
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
FOOTBALL_API_KEY   = os.environ.get("FOOTBALL_API_KEY", "")
ODDS_API_KEY       = os.environ.get("ODDS_API_KEY", "")

# API 端點
FOOTBALL_API_BASE  = "https://api-football-v1.p.rapidapi.com/v3"
ODDS_API_BASE      = "https://api.the-odds-api.com/v4"

# 聯賽配置
LEAGUES = {
    39: {"name": "English Premier League", "emoji": "🇬🇧", "abbr": "EPL", "sport": "soccer_epl"},
    140: {"name": "La Liga", "emoji": "🇪🇸", "abbr": "La Liga", "sport": "soccer_spain_la_liga"},
    135: {"name": "Serie A", "emoji": "🇮🇹", "abbr": "Serie A", "sport": "soccer_italy_serie_a"},
    78: {"name": "Bundesliga", "emoji": "🇩🇪", "abbr": "BL", "sport": "soccer_germany_bundesliga"},
    61: {"name": "Ligue 1", "emoji": "🇫🇷", "abbr": "L1", "sport": "soccer_france_ligue_1"},
}

SEASON = 2024


# ── Odds API 工具 ───────────────────────────────────────────

class OddsAPIManager:
    """賭盤賠率 API 管理"""
    
    @staticmethod
    def get_odds(sport: str = "soccer_epl") -> dict:
        """
        獲取賭盤賠率
        sport: 聯賽代碼 (見 LEAGUES 配置)
        返回: {
            "match_id": {
                "home_team": "team1",
                "away_team": "team2",
                "bookmakers": [
                    {
                        "name": "DraftKings",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "team1", "price": 1.95},
                                    {"name": "team2", "price": 2.10}
                                ]
                            },
                            {
                                "key": "spreads",
                                "outcomes": [...]
                            }
                        ]
                    }
                ]
            }
        }
        """
        try:
            params = {
                "apiKey": ODDS_API_KEY,
                "sport": sport,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "decimal",
            }
            
            resp = requests.get(
                f"{ODDS_API_BASE}/sports/{sport}/odds",
                params=params,
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            
            logger.info(f"獲取 {sport} 賠率：{len(data.get('data', []))} 場比賽")
            return {"success": True, "data": data.get("data", [])}
            
        except Exception as e:
            logger.warning(f"無法獲取賭盤數據 ({sport}): {e}")
            return {"success": False, "data": []}
    
    @staticmethod
    def parse_odds(odds_data: dict) -> dict:
        """
        解析賭盤數據
        """
        result = {}
        
        for game in odds_data.get("data", []):
            match_id = f"{game.get('home_team')}_{game.get('away_team')}"
            
            result[match_id] = {
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "commence_time": game.get("commence_time"),
                "bookmakers": []
            }
            
            for bookmaker in game.get("bookmakers", [])[:3]:  # 只取前 3 個博彩公司
                bm_data = {
                    "name": bookmaker.get("title"),
                    "markets": {}
                }
                
                for market in bookmaker.get("markets", []):
                    market_key = market.get("key")
                    outcomes = market.get("outcomes", [])
                    
                    if market_key == "h2h":
                        bm_data["markets"]["moneyline"] = {
                            o.get("name"): o.get("price") for o in outcomes
                        }
                    elif market_key == "spreads":
                        bm_data["markets"]["spreads"] = {
                            o.get("name"): {
                                "point": o.get("point"),
                                "price": o.get("price")
                            } for o in outcomes
                        }
                    elif market_key == "totals":
                        bm_data["markets"]["totals"] = {
                            o.get("name"): {
                                "point": o.get("point"),
                                "price": o.get("price")
                            } for o in outcomes
                        }
                
                result[match_id]["bookmakers"].append(bm_data)
        
        return result


# ── Football API 工具 ───────────────────────────────────────────

def get_headers():
    """返回 API 請求頭"""
    return {
        "x-rapidapi-key": FOOTBALL_API_KEY,
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com"
    }


def get_matches_by_date(date_str: str) -> dict:
    """獲取指定日期的所有比賽"""
    try:
        params = {"date": date_str}
        resp = requests.get(
            f"{FOOTBALL_API_BASE}/fixtures",
            headers=get_headers(),
            params=params,
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"無法取得比賽數據 ({date_str}): {e}")
        return {"response": []}


def get_match_events(fixture_id: int) -> dict:
    """獲取比賽事件"""
    try:
        resp = requests.get(
            f"{FOOTBALL_API_BASE}/fixtures/events",
            headers=get_headers(),
            params={"fixture": fixture_id},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"無法取得比賽事件 ({fixture_id}): {e}")
        return {"response": []}


def get_team_form(team_id: int) -> str:
    """獲取球隊近期戰績"""
    try:
        resp = requests.get(
            f"{FOOTBALL_API_BASE}/teams/statistics",
            headers=get_headers(),
            params={"team": team_id, "season": SEASON},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("response"):
            form = data["response"].get("form", "")
            return form if form else "N/A"
        return "N/A"
    except Exception as e:
        logger.warning(f"無法取得球隊戰績: {e}")
        return "N/A"


# ── 資料解析 ───────────────────────────────────────────

def filter_league_matches(matches: list) -> list:
    """過濾只保留目標聯賽的比賽"""
    filtered = []
    for match in matches:
        league_id = match.get("league", {}).get("id")
        if league_id in LEAGUES:
            filtered.append(match)
    return filtered


def parse_match(match_data: dict) -> dict:
    """解析比賽數據"""
    fixture = match_data.get("fixture", {})
    teams = match_data.get("teams", {})
    goals = match_data.get("goals", {})
    league = match_data.get("league", {})
    
    home_team = teams.get("home", {})
    away_team = teams.get("away", {})
    
    return {
        "fixture_id": fixture.get("id"),
        "date": fixture.get("date"),
        "status": fixture.get("status", {}).get("short", "?"),
        "home_id": home_team.get("id"),
        "home_name": home_team.get("name", "Unknown"),
        "away_id": away_team.get("id"),
        "away_name": away_team.get("name", "Unknown"),
        "home_score": goals.get("home", 0),
        "away_score": goals.get("away", 0),
        "league_id": league.get("id"),
        "league_name": LEAGUES.get(league.get("id"), {}).get("abbr", ""),
        "sport": LEAGUES.get(league.get("id"), {}).get("sport", ""),
        "completed": fixture.get("status", {}).get("short") in ["FT", "AET", "PEN"],
    }


def get_goals_and_assists(fixture_id: int) -> dict:
    """獲取進球者和助攻者"""
    events = get_match_events(fixture_id)
    goals = []
    assists = []
    
    for event in events.get("response", []):
        if event.get("type") == "Goal":
            goals.append({
                "player": event.get("player", {}).get("name", "Unknown"),
                "team": event.get("team", {}).get("name", "Unknown"),
                "minute": event.get("time", {}).get("elapsed", "?"),
                "assist": event.get("assist", {}).get("name") if event.get("assist") else None
            })
    
    return {"goals": goals, "assists": assists}


# ── 格式化 ─────────────────────────────────────────────────────

def format_odds(match_key: str, odds_map: dict) -> str:
    """格式化賭盤信息"""
    if match_key not in odds_map:
        return ""
    
    odds_data = odds_map[match_key]
    lines = ["💰 *賭盤賠率*"]
    
    for bm in odds_data.get("bookmakers", [])[:2]:  # 只顯示前 2 個博彩公司
        bm_name = bm.get("name", "Unknown")
        lines.append(f"\n  {bm_name}:")
        
        # 勝負賠率 (Moneyline)
        if "moneyline" in bm.get("markets", {}):
            ml = bm["markets"]["moneyline"]
            home_price = ml.get(odds_data["home_team"], "N/A")
            away_price = ml.get(odds_data["away_team"], "N/A")
            lines.append(f"    勝負: {away_price} / {home_price}")
        
        # 讓分盤 (Spreads)
        if "spreads" in bm.get("markets", {}):
            spreads = bm["markets"]["spreads"]
            for team, data in spreads.items():
                point = data.get("point", 0)
                price = data.get("price", "N/A")
                lines.append(f"    {team} {point:+.1f} @ {price}")
        
        # 大小分 (Totals)
        if "totals" in bm.get("markets", {}):
            totals = bm["markets"]["totals"]
            for ou, data in totals.items():
                point = data.get("point", 0)
                price = data.get("price", "N/A")
                lines.append(f"    {ou} {point} @ {price}")
    
    return "\n".join(lines)


def format_result_block(match: dict, odds_map: dict) -> str:
    """格式化已完成的比賽結果"""
    home, away = match["home_name"], match["away_name"]
    hs, as_ = match["home_score"], match["away_score"]
    
    if match["completed"]:
        winner_emoji = "✅" if hs > as_ else "⚽" if hs < as_ else "🤝"
        score_line = f"*{away} {as_}  —  {hs} {home}*  {winner_emoji}"
    else:
        score_line = f"*{away} vs {home}*  ⏳ {match['status']}"
    
    lines = [f"{LEAGUES.get(match['league_id'], {}).get('emoji', '')} {score_line}"]
    
    # 獲取進球者信息
    if match["completed"] and (hs > 0 or as_ > 0):
        goals_data = get_goals_and_assists(match["fixture_id"])
        goals = goals_data.get("goals", [])
        
        if goals:
            lines.append("⚽ *進球者*")
            for goal in goals:
                assist = f" (助攻: {goal['assist']})" if goal.get("assist") else ""
                lines.append(f"  {goal['minute']}' {goal['player']}{assist}")
    
    # 添加賭盤信息
    match_key = f"{away}_{home}"
    odds_info = format_odds(match_key, odds_map)
    if odds_info:
        lines.append(odds_info)
    
    return "\n".join(lines)


def format_preview_block(match: dict, odds_map: dict) -> str:
    """格式化比賽預測"""
    home, away = match["home_name"], match["away_name"]
    home_id, away_id = match["home_id"], match["away_id"]
    
    lines = [f"{LEAGUES.get(match['league_id'], {}).get('emoji', '')} *{away} vs {home}*"]
    
    # 戰績
    home_form = get_team_form(home_id)
    away_form = get_team_form(away_id)
    
    if home_form != "N/A" or away_form != "N/A":
        lines.append("📊 *近期戰績*")
        lines.append(f"  {home}: {home_form}")
        lines.append(f"  {away}: {away_form}")
    
    # 賭盤信息
    match_key = f"{away}_{home}"
    odds_info = format_odds(match_key, odds_map)
    if odds_info:
        lines.append(odds_info)
    
    return "\n".join(lines)


# ── 主報告生成 ─────────────────────────────────────────

def build_full_report(today_str: str, tomorrow_str: str) -> str:
    """構建完整報告"""
    logger.info("抓取比賽數據…")
    
    today_data = get_matches_by_date(today_str)
    tomorrow_data = get_matches_by_date(tomorrow_str)
    
    today_matches = filter_league_matches([parse_match(m) for m in today_data.get("response", [])])
    tomorrow_matches = filter_league_matches([parse_match(m) for m in tomorrow_data.get("response", [])])
    
    # 獲取賭盤信息（如果有 API Key）
    odds_map = {}
    if ODDS_API_KEY:
        logger.info("抓取賭盤數據…")
        for league_id, league_info in LEAGUES.items():
            odds_data = OddsAPIManager.get_odds(league_info["sport"])
            if odds_data["success"]:
                parsed_odds = OddsAPIManager.parse_odds(odds_data)
                odds_map.update(parsed_odds)
        logger.info(f"獲取 {len(odds_map)} 場比賽的賭盤信息")
    
    sections = []
    
    # ── 昨日賽果 ──
    if today_matches:
        sections.append(f"⚽ *昨日賽果 — {today_str}*（共 {len(today_matches)} 場）\n")
        for match in today_matches:
            sections.append(format_result_block(match, odds_map))
            sections.append("─" * 28)
    else:
        sections.append(f"⚽ *昨日賽果 — {today_str}*\n\n昨天沒有比賽 🏖️")
        sections.append("─" * 28)
    
    # ── 今日預測 ──
    if tomorrow_matches:
        sections.append(f"\n🔮 *今日預測 — {tomorrow_str}*（共 {len(tomorrow_matches)} 場）\n")
        for match in tomorrow_matches:
            sections.append(format_preview_block(match, odds_map))
            sections.append("─" * 28)
    else:
        sections.append(f"\n🔮 *今日預測 — {tomorrow_str}*\n\n今天沒有比賽 🏖️")
    
    sections.append("\n⚠️ _預測及賭盤信息僅供參考，非投注依據_\n_由 Football Bot (with Odds API) 自動產生 🤖_")
    return "\n".join(sections)


async def main():
    tw_now = datetime.now(timezone.utc) + timedelta(hours=8)
    today_str = (tw_now - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_str = tw_now.strftime("%Y-%m-%d")
    
    logger.info("產生報告：昨日 %s + 今日 %s", today_str, tomorrow_str)
    
    try:
        report = build_full_report(today_str, tomorrow_str)
    except Exception as e:
        import traceback
        report = f"⚠️ 取得報告失敗：{e}"
        logger.error(report)
        logger.error(traceback.format_exc())
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=report,
        parse_mode=ParseMode.MARKDOWN
    )
    logger.info("已發送 ✅")


if __name__ == "__main__":
    asyncio.run(main())
