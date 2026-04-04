import os
import re
import base64
import pytz
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from groq import Groq
from sqlalchemy import func
from database import SessionLocal, CalorieLog, WaterLog, init_db
from apscheduler.schedulers.background import BackgroundScheduler

init_db()
load_dotenv()

app = FastAPI()
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ── TIMEZONE & CONFIG ────────────────────────────────────────────────────────
PKT            = pytz.timezone("Asia/Karachi")
BRIDGE_URL     = "http://127.0.0.1:3001/send"
REMINDER_TARGET = os.getenv("TARGET_GROUP_ID")
WATER_GOAL     = 10

# ── WATER NLP PATTERNS ───────────────────────────────────────────────────────
_WATER_RE = re.compile(
    r'(?i)'
    r'(?:(?:i\s+)?(?:just\s+)?(?:had|drank|drunk|finished|downed|drinking)\s+)?'
    r'(?:(?:a|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+)?'
    r'glass(?:es)?\s+of\s+water'
    r'|(?:i\s+)?(?:just\s+)?drank\s+(?:(?:a|some|\d+)\s+)?(?:glass(?:es)?\s+of\s+)?water'
)
_WORD_TO_NUM = {
    'a':1,'one':1,'two':2,'three':3,'four':4,'five':5,
    'six':6,'seven':7,'eight':8,'nine':9,'ten':10
}

def detect_water_log(text):
    """Returns glass count if text is a water log, else None."""
    if _WATER_RE.search(text):
        m = re.search(r'(\d+)\s+glass(?:es)?\s+of\s+water', text, re.IGNORECASE)
        if m:
            return min(int(m.group(1)), WATER_GOAL)
        w = re.search(
            r'(a|one|two|three|four|five|six|seven|eight|nine|ten)\s+glass(?:es)?\s+of\s+water',
            text, re.IGNORECASE
        )
        if w:
            return _WORD_TO_NUM.get(w.group(1).lower(), 1)
        return 1
    return None

# ── WATER HELPERS ────────────────────────────────────────────────────────────
def get_water_today(phone_number):
    db = SessionLocal()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.query(func.sum(WaterLog.glasses)).filter(
        WaterLog.user_phone == phone_number,
        WaterLog.timestamp >= today_start
    ).scalar() or 0
    db.close()
    return int(total)

def calculate_water_streak(phone_number):
    db = SessionLocal()
    logs = db.query(WaterLog).filter(WaterLog.user_phone == phone_number).all()
    db.close()
    if not logs:
        return 0
    daily = defaultdict(int)
    for log in logs:
        daily[log.timestamp.date()] += log.glasses
    qualifying = sorted([d for d, t in daily.items() if t >= WATER_GOAL], reverse=True)
    if not qualifying:
        return 0
    today = datetime.now().date()
    streak, check = 0, today
    if qualifying[0] != today:
        if qualifying[0] == today - timedelta(days=1):
            check = today - timedelta(days=1)
        else:
            return 0
    for d in qualifying:
        if d == check:
            streak += 1
            check -= timedelta(days=1)
        elif d < check:
            break
    return streak

def build_water_bar(glasses):
    filled = min(glasses, WATER_GOAL)
    return "🔵" * filled + "⚪" * (WATER_GOAL - filled)

def get_hydro_tip(glasses, hour):
    if glasses >= WATER_GOAL:
        return "🏆 *GOAL ACHIEVED!* Incredible hydration! 💧"
    rem = WATER_GOAL - glasses
    g = lambda n: "glass" if n == 1 else "glasses"
    if hour < 10 and glasses < 2:
        return "🌅 *Morning Boost:* Drink 2 glasses before your first meal to kick-start your metabolism!"
    if 14 <= hour < 16 and glasses < 5:
        return "⚠️ *Rule of Halves Alert!* You should have ~5 glasses by 2PM to avoid midnight trips!"
    if hour >= 20:
        return f"🌙 *Evening Push:* Only *{rem}* {g(rem)} left — you've got this!"
    return f"💪 *{rem}* {g(rem)} left to hit your goal. Stay hydrated!"

# ── PROACTIVE REMINDER ───────────────────────────────────────────────────────
def send_water_reminder():
    """Runs every 90 min. Only fires 7AM–10PM PKT, stops when goal is hit."""
    now = datetime.now(PKT)
    hour = now.hour
    if not (7 <= hour < 22):
        return
    glasses = get_water_today(REMINDER_TARGET)
    if glasses >= WATER_GOAL:
        return
    bar = build_water_bar(glasses)
    tip = get_hydro_tip(glasses, hour)
    if hour == 7 and glasses == 0:
        msg = (
            "━━━━━━━━━━━━━━━\n"
            "💧 *GOOD MORNING, ZARA!*\n"
            "━━━━━━━━━━━━━━━\n"
            "🌅 Start your metabolism: drink 2 glasses\n"
            "of water before your first meal!\n\n"
            f"{bar}\n"
            f"0 / {WATER_GOAL} glasses — let's go! 🚀\n"
            "━━━━━━━━━━━━━━━"
        )
    else:
        msg = (
            "━━━━━━━━━━━━━━━\n"
            "💧 *HYDRATION REMINDER*\n"
            "━━━━━━━━━━━━━━━\n"
            f"{bar}\n"
            f"{glasses} / {WATER_GOAL} glasses today\n\n"
            f"{tip}\n"
            "━━━━━━━━━━━━━━━"
        )
    try:
        requests.post(BRIDGE_URL, json={"message": msg}, timeout=5)
        print(f"💧 Reminder sent ({glasses}/{WATER_GOAL} @ {now.strftime('%H:%M')} PKT)")
    except Exception as e:
        print(f"⚠️ Reminder failed: {e}")

# ── SCHEDULER (every 90 min) ─────────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone=PKT)
scheduler.add_job(send_water_reminder, "interval", minutes=90, id="water_reminder")
scheduler.start()
print("⏰ Water reminder scheduler started (every 90 min, 7AM–10PM PKT).")

# ── CALORIE/EXERCISE HELPERS ─────────────────────────────────────────────────
def get_daily_metrics(phone_number):
    db = SessionLocal()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    food_metrics = db.query(
        func.sum(CalorieLog.calories),
        func.sum(CalorieLog.protein),
        func.sum(CalorieLog.carbs),
        func.sum(CalorieLog.fats)
    ).filter(
        CalorieLog.user_phone == phone_number,
        CalorieLog.timestamp >= today_start,
        CalorieLog.is_exercise == 0
    ).first()
    burned_total = db.query(func.sum(CalorieLog.calories)).filter(
        CalorieLog.user_phone == phone_number,
        CalorieLog.timestamp >= today_start,
        CalorieLog.is_exercise == 1
    ).scalar() or 0
    db.close()
    return {
        "calories": food_metrics[0] or 0,
        "protein":  food_metrics[1] or 0,
        "carbs":    food_metrics[2] or 0,
        "fats":     food_metrics[3] or 0,
        "burned":   burned_total
    }

def calculate_streak(phone_number, exercise_only=False):
    db = SessionLocal()
    is_ex = 1 if exercise_only else 0
    logs = db.query(CalorieLog).filter(
        CalorieLog.user_phone == phone_number,
        CalorieLog.is_exercise == is_ex
    ).order_by(CalorieLog.timestamp.desc()).all()
    db.close()
    if not logs:
        return 0
    unique_dates = sorted(list(set(log.timestamp.date() for log in logs)), reverse=True)
    today = datetime.utcnow().date()
    streak = 0
    current_check_date = today
    if unique_dates and unique_dates[0] != today:
        if unique_dates[0] == today - timedelta(days=1):
            current_check_date = today - timedelta(days=1)
        else:
            return 0
    for d in unique_dates:
        if d == current_check_date:
            streak += 1
            current_check_date -= timedelta(days=1)
        elif d > current_check_date:
            continue
        else:
            break
    return streak

def validate_macro_math(p, c, f, calories):
    calculated_cals = (p * 4) + (c * 4) + (f * 9)
    margin = 0.15
    if not (calculated_cals * (1 - margin) <= calories <= calculated_cals * (1 + margin)):
        return int(calculated_cals)
    return calories

def analyze_food_with_ai(query):
    prompt = (
        "Role: Expert Nutritionist & Fitness Coach.\n"
        f"Analyze '{query}'.\n"
        "PROCESS:\n"
        "1. <thinking>: Identify the food, likely restaurant (look for clues in text), and portion size.\n"
        "2. <calculation>: Break down macros (P/C/F) based on that portion.\n"
        "3. <final>: Provide the output in the required format.\n\n"
        "RULES:\n"
        "- If no portion is mentioned, assume 1 standard restaurant serving.\n"
        "- If Pakistani cuisine: increase oil/fats by 15% compared to western equivalents.\n"
        "- Output ONLY the final log format, do not show your <thinking> tags.\n\n"
        "Log Type: [Food/Exercise]\n"
        "1. Start with 'Log Type: Food' or 'Log Type: Exercise'.\n"
        "2. NO conversational filler.\n"
        "3. List identified items clearly.\n"
        "4. FORMAT ENDING EXACTLY:\n"
        "Total Macros: Protein: [g], Carbs: [g], Fats: [g]\n"
        "Total Estimated: [number] calories"
    )
    completion = groq_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
    )
    return completion.choices[0].message.content

def analyze_image_with_ai(base64_image, user_note=""):
    try:
        MODEL_ID = "meta-llama/llama-4-scout-17b-16e-instruct"
        main_prompt = (
            "Identify the food items or exercise in this image.\n"
            "DETECTION RULES:\n"
            "1. Look for branding: Check packaging, napkins, or cups for restaurant logos (e.g., Savour, Cheezious, Pizza Max, KFC).\n"
            "2. Portions: Estimate the size relative to the plate or surrounding objects (e.g., '1.5 cups of rice', '8-inch pizza').\n"
            "3. Regional Context: The user is in Pakistan. If you identify a 'Crown Crust' or 'Stuffed Crust' pizza, account for heavy mayo/ranch and cheese edges (+350 kcal per large slice).\n"
            "4. Hidden Fats: If the food looks oily or glistening (common in local Karahis or Biryanis), increase the Fat estimate by 20%.\n"
        )
        if user_note:
            main_prompt += f"CRITICAL USER NOTE: {user_note}. Use this to override visual estimates.\n"
        completion = groq_client.chat.completions.create(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        f"{main_prompt}\n"
                        "RULES:\n"
                        "1. NO introductory text.\n"
                        "2. List food/exercise briefly (no per-item macros).\n"
                        "3. Specify 'Log Type: Food' OR 'Log Type: Exercise' at the start.\n"
                        "4. END with EXACTLY this format:\n"
                        "Total Macros: Protein: [g], Carbs: [g], Fats: [g]\n"
                        "Total Estimated: [number] calories"
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ],
            }],
            model=MODEL_ID,
        )
        return completion.choices[0].message.content
    except Exception as e:
        print(f"❌ Groq API Error: {e}")
        return f"Vision Error: Please check if {MODEL_ID} is still active."


@app.post("/webhook")
async def receive_whatsapp_message(request: Request):
    try:
        data     = await request.json()
        body     = data.get("Body", "").strip()
        sender   = data.get("From", "unknown")
        image_b64 = data.get("ImageData")

        # ── 1. COMMANDS ───────────────────────────────────────────────────────

        if body.lower() == "!waterstatus":
            glasses      = get_water_today(sender)
            water_streak = calculate_water_streak(sender)
            bar          = build_water_bar(glasses)
            now_pkt      = datetime.now(PKT)
            tip          = get_hydro_tip(glasses, now_pkt.hour)
            status_msg   = (
                "━━━━━━━━━━━━━━━\n"
                "💧 *WATER STATUS*\n"
                "━━━━━━━━━━━━━━━\n"
                f"{bar}\n"
                f"*{glasses} / {WATER_GOAL} glasses today*\n\n"
                f"{tip}\n\n"
                "💡 *Activity Tip:* Add 1 extra glass for every\n"
                "30 min of jumping rope or Pilates!\n\n"
                f"🗓️ *Water Streak:* {water_streak} day{'s' if water_streak != 1 else ''} 🔥\n"
                "━━━━━━━━━━━━━━━"
            )
            return Response(content=status_msg, media_type="text/plain")

        elif body.lower() == "!summary":
            db = SessionLocal()
            target = 1200
            report = "📝 *WEEKLY CALORIE SUMMARY*\n━━━━━━━━━━━━━━━\n"
            for i in range(6, -1, -1):
                day_date  = (datetime.utcnow() - timedelta(days=i)).date()
                day_start = datetime.combine(day_date, datetime.min.time())
                day_end   = datetime.combine(day_date, datetime.max.time())
                day_total = db.query(func.sum(CalorieLog.calories)).filter(
                    CalorieLog.user_phone == sender,
                    CalorieLog.timestamp >= day_start,
                    CalorieLog.timestamp <= day_end
                ).scalar() or 0
                date_str = day_date.strftime("%a, %d %b")
                report  += f"📅 {date_str}: ```{day_total} / {target} kcal```\n"
            db.close()
            report += "━━━━━━━━━━━━━━━"
            return Response(content=report, media_type="text/plain")

        elif body.lower() == "!undo":
            db = SessionLocal()
            last_log = db.query(CalorieLog).filter(
                CalorieLog.user_phone == sender
            ).order_by(CalorieLog.timestamp.desc()).first()
            if not last_log:
                db.close()
                return Response(content="⚠️ No recent entries found to undo.", media_type="text/plain")
            food_name = last_log.food_item
            food_cals = last_log.calories
            db.delete(last_log)
            db.commit()
            db.close()
            daily_metrics = get_daily_metrics(sender)
            daily_total   = daily_metrics["calories"]
            food_streak   = calculate_streak(sender, exercise_only=False)
            target = 1200
            if daily_total <= target:
                status_text = f"🟢 *REMAINING:* ```{target - daily_total} kcal```"
            else:
                status_text = f"🔴 *OVERFLOW:* ```{daily_total - target} kcal```"
            undo_message = (
                f"━━━━━━━━━━━━━━━\n"
                f"↩️ *UNDO SUCCESSFUL*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Deleted: {food_name[:20]} ({food_cals} kcal)\n\n"
                f"📊 *NEW DAILY TOTAL:* ```{daily_total} / {target} kcal```\n"
                f"{status_text}\n"
                f"🍕 *FOOD STREAK:* ```{food_streak} days```\n"
                f"━━━━━━━━━━━━━━━"
            )
            return Response(content=undo_message, media_type="text/plain")

        elif body.lower() in ("!commands", "!command"):
            help_text = (
                "📜 *AVAILABLE COMMANDS*\n"
                "━━━━━━━━━━━━━━━\n"
                "• *!today* - Today's full progress\n"
                "• *!waterstatus* - Water progress & tips\n"
                "• *!summary* - Weekly calorie report\n"
                "• *!undo* - Delete last entry\n"
                "• *!dayhistory* - Clear today's history\n"
                "• *!delhistory* - Reset entire history\n"
                "• *!commands* - Show this list\n"
                "━━━━━━━━━━━━━━━\n"
                "💡 Say *'had a glass of water'* to log water!\n"
                "💡 Send a food photo or text to log calories!"
            )
            return Response(content=help_text, media_type="text/plain")

        elif body.lower() == "!today":
            metrics      = get_daily_metrics(sender)
            food_streak  = calculate_streak(sender, exercise_only=False)
            ex_streak    = calculate_streak(sender, exercise_only=True)
            water_streak = calculate_water_streak(sender)
            glasses      = get_water_today(sender)
            bar          = build_water_bar(glasses)
            t_cal, t_prot, t_carb, t_fat = 1200, 75, 150, 34
            daily_food  = metrics["calories"]
            bonus_cals  = metrics["burned"]
            if daily_food <= t_cal:
                status_text = f"🟢 *REMAINING:* ```{t_cal - daily_food} kcal```"
            else:
                net_excess  = (daily_food - t_cal) - bonus_cals
                status_text = "🟢 *OVERFLOW COVERED BY BONUS!*" if net_excess <= 0 else f"🔴 *OVERFLOW:* ```{net_excess} kcal```"
            today_report = (
                f"━━━━━━━━━━━━━━━\n"
                f"📅 *TODAY'S SUMMARY*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥩 *MACROS TD:* \n"
                f"P: {metrics['protein']}/{t_prot}g | C: {metrics['carbs']}/{t_carb}g | F: {metrics['fats']}/{t_fat}g\n\n"
                f"📊 *DAILY TOTAL STACK:* \n"
                f"Consumed: {daily_food} / {t_cal} kcal\n"
                f"🔥 Bonus: {bonus_cals} kcal burned\n\n"
                f"{status_text}\n\n"
                f"💧 *WATER TODAY:*\n"
                f"{bar}\n"
                f"{glasses} / {WATER_GOAL} glasses\n\n"
                f"🌟 *STREAKS:* \n"
                f"🍕 Food: {food_streak}d | 💪 Exercise: {ex_streak}d | 💧 Water: {water_streak}d\n"
                f"━━━━━━━━━━━━━━━"
            )
            return Response(content=today_report, media_type="text/plain")

        elif body.lower() == "!dayhistory":
            db = SessionLocal()
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            db.query(CalorieLog).filter(
                CalorieLog.user_phone == sender,
                CalorieLog.timestamp >= today_start
            ).delete()
            db.commit()
            db.close()
            return Response(content="🗑️ *DAY HISTORY CLEARED*\nAll logs for today have been deleted.", media_type="text/plain")

        elif body.lower() == "!delhistory":
            db = SessionLocal()
            db.query(CalorieLog).filter(CalorieLog.user_phone == sender).delete()
            db.commit()
            db.close()
            return Response(content="🔥 *ENTIRE HISTORY DELETED*\nAll your calorie logs have been wiped from the database.", media_type="text/plain")

        # ── 2. WATER NLP DETECTION (before AI) ───────────────────────────────
        water_glasses = detect_water_log(body) if not image_b64 else None
        if water_glasses:
            db = SessionLocal()
            db.add(WaterLog(user_phone=sender, glasses=water_glasses))
            db.commit()
            db.close()
            glasses      = get_water_today(sender)
            water_streak = calculate_water_streak(sender)
            bar          = build_water_bar(glasses)
            now_pkt      = datetime.now(PKT)
            tip          = get_hydro_tip(glasses, now_pkt.hour)
            # Celebration if goal just hit
            if glasses >= WATER_GOAL:
                header = f"🏆 *GOAL COMPLETE — {WATER_GOAL}/{WATER_GOAL} GLASSES!*"
            else:
                header = f"💧 *WATER LOGGED* (+{water_glasses} glass{'es' if water_glasses > 1 else ''})"
            water_reply = (
                f"━━━━━━━━━━━━━━━\n"
                f"{header}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{bar}\n"
                f"*{glasses} / {WATER_GOAL} glasses today*\n\n"
                f"{tip}\n\n"
                f"🗓️ *Water Streak:* {water_streak} day{'s' if water_streak != 1 else ''} 🔥\n"
                f"━━━━━━━━━━━━━━━"
            )
            return Response(content=water_reply, media_type="text/plain")

        # ── 3. AI ANALYSIS (food / exercise / image) ──────────────────────────
        if image_b64:
            print(f"📸 Combined Entry: Photo + '{body}'")
            ai_response = analyze_image_with_ai(image_b64, user_note=body)
            log_text    = f"📸 Photo ({body})" if body else "📸 Photo Entry"
        else:
            print(f"✍️ TEXT DETECTED: Analyzing '{body}'...")
            ai_response = analyze_food_with_ai(body)
            log_text    = body

        # ── 4. EXTRACT CALORIES & MACROS ─────────────────────────────────────
        is_exercise = 1 if "Log Type: Exercise" in ai_response else 0

        matches_cal    = re.findall(r"(?:Total Estimated|Total|Estimated):\s*~?(\d+)", ai_response, re.IGNORECASE)
        calories_value = int(matches_cal[-1]) if matches_cal else 0

        p_match = re.search(r"Protein: (\d+)", ai_response, re.IGNORECASE)
        c_match = re.search(r"Carbs: (\d+)",   ai_response, re.IGNORECASE)
        f_match = re.search(r"Fats: (\d+)",    ai_response, re.IGNORECASE)

        p_val = int(p_match.group(1)) if p_match else 0
        c_val = int(c_match.group(1)) if c_match else 0
        f_val = int(f_match.group(1)) if f_match else 0

        if is_exercise == 0:
            calories_value = validate_macro_math(p_val, c_val, f_val, calories_value)

        print(f"DEBUG: {calories_value}kcal P:{p_val} C:{c_val} F:{f_val} Ex:{is_exercise}")

        # ── 5. SAVE TO DATABASE ───────────────────────────────────────────────
        db = SessionLocal()
        db.add(CalorieLog(
            user_phone=sender, food_item=log_text, calories=calories_value,
            protein=p_val, carbs=c_val, fats=f_val, is_exercise=is_exercise
        ))
        db.commit()
        db.close()

        # ── 6. BUILD REPLY ────────────────────────────────────────────────────
        metrics      = get_daily_metrics(sender)
        food_streak  = calculate_streak(sender, exercise_only=False)
        ex_streak    = calculate_streak(sender, exercise_only=True)
        water_streak = calculate_water_streak(sender)
        glasses      = get_water_today(sender)

        t_cal, t_prot, t_carb, t_fat = 1200, 75, 150, 34
        daily_food  = metrics["calories"]
        bonus_cals  = metrics["burned"]

        if daily_food <= t_cal:
            status_text = f"🟢 *REMAINING:* ```{t_cal - daily_food} kcal```"
        else:
            net_excess  = (daily_food - t_cal) - bonus_cals
            status_text = "🟢 *OVERFLOW COVERED BY BONUS!*" if net_excess <= 0 else f"🔴 *OVERFLOW:* ```{net_excess} kcal```"

        header = "⚡ *EXERCISE REPORT*" if is_exercise else "🍽️ *NUTRITION REPORT*"
        macro_section = (
            f"🥩 *MACROS TD:* \n"
            f"P: {metrics['protein']}/{t_prot}g | C: {metrics['carbs']}/{t_carb}g | F: {metrics['fats']}/{t_fat}g\n\n"
        ) if not is_exercise else ""

        final_message = (
            f"━━━━━━━━━━━━━━━\n"
            f"{header}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{ai_response}\n\n"
            f"{macro_section}"
            f"📊 *DAILY TOTAL STACK:* \n"
            f"Consumed: {daily_food} / {t_cal} kcal\n"
            f"🔥 Bonus: {bonus_cals} kcal burned\n\n"
            f"{status_text}\n"
            f"🌟 *STREAKS:* \n"
            f"🍕 Food: {food_streak}d | 💪 Ex: {ex_streak}d | 💧 Water: {water_streak}d\n"
            f"━━━━━━━━━━━━━━━"
        )
        return Response(content=final_message, media_type="text/plain")

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        return Response(content=f"❌ Error: {str(e)}", media_type="text/plain")