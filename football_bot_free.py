#!/usr/bin/env python3
"""
Football Bot with Football-Data.org - 足球機器人 + 免費 API
每天台灣時間 14:00 發送：昨日賽果 + 今日預測 + 賭盤賔率
支持聯賽：英超、西甲、意甲、德甲、法甲、中超、日本
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
ODDS_API_KEY       = os.environ.get("ODDS_API_KEY", "")

# API 端點
FOOTBALL_API_BASE  = "https://api.football-data.org/v4"
ODDS_API_BASE      = "https://api.the-odds-api.com/v4"

# 聯賽配置 (competition_code: league_info)
LEAGUES = {
    "PL": {"name": "English Premier League", "emoji": "🇬🇧", "abbr": "EPL", "sport": "soccer_epl"},
    "LA": {"name": "La Liga", "emoji": "🇪🇸", "abbr": "La Liga", "sport": "soccer_spain_la_liga"},
    "SA": {"name": "Serie A", "emoji": "🇮🇹", "abbr": "Serie A", "sport": "soccer_italy_serie_a"},
    "BL1": {"name": "Bundesliga", "emoji": "🇩🇪", "abbr": "BL", "sport": "soccer_germany_bundesliga"},
    "FL1": {"name": "Ligue 1", "emoji": "🇫🇷", "abbr": "L1", "sport": "soccer_france_ligue_1"},
    "CSL": {"name": "Chinese Super League", "emoji": "🇨🇳", "abbr": "CSL", "sport": "soccer_china_super_league"},
    "JL1": {"name": "J-League", "emoji": "🇯🇵", "abbr": "J-League", "sport": "soccer_japan_j_league"},
}

SEASON = 2024


# ── Football-Data.org API 工具 ───────────────────────────────────────────

def get_matches_by_date(date_str: str) -> list:
    """
    獲取指定日期的所有比賽
    date_str: YYYY-MM-DD 格式
    """
    try:
        matches = []
        for code in LEAGUES.keys():
            params = {
                "dateFrom": date_str,
                "dateTo": date_str,
                "status": "FINISHED,LIVE,SCHEDULED"
            }
            resp = requests.get(
                f"{FOOTBALL_API_BASE}/competitions/{code}/matches",
                params=params,
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            
            for match in data.get("matches", []):
                match["league_code"] = code
                matches.append(match)
        
        logger.info(f"獲取 {date_str} 的比賽：{len(matches)} 場")
        return matches
    except Exception as e:
        logger.warning(f"無法取得比賽數據 ({date_str}): {e}")
        return []


# ── Odds API 工具 ───────────────────────────────────────────

class OddsAPIManager:
    """賭盤賔率 API 管理"""
    
    @staticmethod
    def get_odds(sport: str = "soccer_epl") -> dict:
        """獲取賭盤賔率"""
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
        """解析賭盤數據"""
        result = {}
        
        for game in odds_data.get("data", []):
            match_id = f"{game.get('home_team')}_{game.get('away_team')}"
            
            result[match_id] = {
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "bookmakers": []
            }
            
            for bookmaker in game.get("bookmakers", [])[:2]:
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


# ── 資料解析 ───────────────────────────────────────────

def parse_match(match_data: dict) -> dict:
    """解析比賽數據"""
    home_team = match_data.get("homeTeam", {})
    away_team = match_data.get("awayTeam", {})
    score = match_data.get("score", {})
    
    return {
        "match_id": match_data.get("id"),
        "date": match_data.get("utcDate"),
        "status": match_data.get("status"),
        "home_name": home_team.get("name", "Unknown"),
        "away_name": away_team.get("name", "Unknown"),
        "home_score": score.get("fullTime", {}).get("home", 0),
        "away_score": score.get("fullTime", {}).get("away", 0),
        "league_code": match_data.get("league_code", ""),
        "league_name": LEAGUES.get(match_data.get("league_code", ""), {}).get("abbr", ""),
        "completed": match_data.get("status") in ["FINISHED"],
    }


# ── 格式化 ─────────────────────────────────────────────────────

def format_odds(match_key: str, odds_map: dict) -> str:
    """格式化賭盤信息"""
    if match_key not in odds_map:
        return ""
    
    odds_data = odds_map[match_key]
    lines = ["💰 *賭盤賔率*"]
    
    for bm in odds_data.get("bookmakers", []):
        bm_name = bm.get("name", "Unknown")
        lines.append(f"\n  {bm_name}:")
        
        if "moneyline" in bm.get("markets", {}):
            ml = bm["markets"]["moneyline"]
            home_price = ml.get(odds_data["home_team"], "N/A")
            away_price = ml.get(odds_data["away_team"], "N/A")
            lines.append(f"    勝負: {away_price} / {home_price}")
    
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
    
    league_emoji = LEAGUES.get(match['league_code'], {}).get('emoji', '')
    lines = [f"{league_emoji} {score_line}"]
    
    # 添加賭盤信息
    match_key = f"{away}_{home}"
    odds_info = format_odds(match_key, odds_map)
    if odds_info:
        lines.append(odds_info)
    
    return "\n".join(lines)


def format_preview_block(match: dict, odds_map: dict) -> str:
    """格式化比賽預測"""
    home, away = match["home_name"], match["away_name"]
    league_emoji = LEAGUES.get(match['league_code'], {}).get('emoji', '')
    
    lines = [f"{league_emoji} *{away} vs {home}*"]
    
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
    
    today_matches = [parse_match(m) for m in get_matches_by_date(today_str)]
    tomorrow_matches = [parse_match(m) for m in get_matches_by_date(tomorrow_str)]
    
    # 過濾已完成的比賽
    today_completed = [m for m in today_matches if m["completed"]]
    tomorrow_scheduled = [m for m in tomorrow_matches if m["status"] == "SCHEDULED"]
    
    # 獲取賭盤信息
    odds_map = {}
    if ODDS_API_KEY:
        logger.info("抓取賭盤數據…")
        for league_code, league_info in LEAGUES.items():
            odds_data = OddsAPIManager.get_odds(league_info["sport"])
            if odds_data["success"]:
                parsed_odds = OddsAPIManager.parse_odds(odds_data)
                odds_map.update(parsed_odds)
    
    sections = []
    
    # ── 昨日賽果 ──
    if today_completed:
        sections.append(f"⚽ *昨日賽果 — {today_str}*（共 {len(today_completed)} 場）\n")
        for match in today_completed:
            sections.append(format_result_block(match, odds_map))
            sections.append("─" * 28)
    else:
        sections.append(f"⚽ *昨日賽果 — {today_str}*\n\n昨天沒有比賽 🏖️")
        sections.append("─" * 28)
    
    # ── 今日預測 ──
    if tomorrow_scheduled:
        sections.append(f"\n🔮 *今日預測 — {tomorrow_str}*（共 {len(tomorrow_scheduled)} 場）\n")
        for match in tomorrow_scheduled:
            sections.append(format_preview_block(match, odds_map))
            sections.append("─" * 28)
    else:
        sections.append(f"\n🔮 *今日預測 — {tomorrow_str}*\n\n今天沒有比賽 🏖️")
    
    sections.append("\n⚠️ _預測及賭盤信息僅供參考，非投注依據_\n_由 Football Bot 自動產生 🤖_")
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
