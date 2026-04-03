import os
import re
import base64
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from groq import Groq
from sqlalchemy import func
from database import SessionLocal, CalorieLog, init_db

init_db()
load_dotenv()

app = FastAPI()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def get_daily_metrics(phone_number):
    db = SessionLocal()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 1. Get Food Totals
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
    
    # 2. Get Exercise Burned Total
    burned_total = db.query(func.sum(CalorieLog.calories)).filter(
        CalorieLog.user_phone == phone_number,
        CalorieLog.timestamp >= today_start,
        CalorieLog.is_exercise == 1
    ).scalar() or 0
    
    db.close()
    return {
        "calories": food_metrics[0] or 0,
        "protein": food_metrics[1] or 0,
        "carbs": food_metrics[2] or 0,
        "fats": food_metrics[3] or 0,
        "burned": burned_total
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

def analyze_food_with_ai(query):
    prompt = (
        f"Nutritionist: Analyze '{query}'.\n"
        "RULES:\n"
        "1. NO introductory or conversational text.\n"
        "2. List food items or exercise briefly (no per-item macros).\n"
        "3. Specify 'Log Type: Food' OR 'Log Type: Exercise' at the start.\n"
        "4. END with EXACTLY this format:\n"
        "Total Macros: Protein: [g], Carbs: [g], Fats: [g]\n"
        "Total Estimated: [number] calories"
    )
    completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
    )
    return completion.choices[0].message.content

def analyze_image_with_ai(base64_image, user_note=""):
    try:
        # THE NEW 2026 VISION MODEL ID
        MODEL_ID = "meta-llama/llama-4-scout-17b-16e-instruct"
        
        # Build prompt: Include user note if they wrote anything extra!
        main_prompt = "Identify the food items OR exercise in this image.\n"
        if user_note:
            main_prompt += f"USER NOTES: The user said: '{user_note}'. Adjust your estimation based on this note.\n"

        completion = client.chat.completions.create(
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
        data = await request.json()
        body = data.get("Body", "").strip()
        sender = data.get("From", "unknown")
        image_b64 = data.get("ImageData")

        # 1. HANDLE COMMANDS FIRST
        if body.lower() == "!summary":
            db = SessionLocal()
            target = 1200
            report = "📝 *WEEKLY CALORIE SUMMARY*\n━━━━━━━━━━━━━━━\n"
            
            # Get data for the last 7 days (including today)
            for i in range(6, -1, -1):
                day_date = (datetime.utcnow() - timedelta(days=i)).date()
                day_start = datetime.combine(day_date, datetime.min.time())
                day_end = datetime.combine(day_date, datetime.max.time())
                
                day_total = db.query(func.sum(CalorieLog.calories)).filter(
                    CalorieLog.user_phone == sender,
                    CalorieLog.timestamp >= day_start,
                    CalorieLog.timestamp <= day_end
                ).scalar() or 0
                
                date_str = day_date.strftime("%a, %d %b")
                report += f"📅 {date_str}: ```{day_total} / {target} kcal```\n"
            
            db.close()
            report += "━━━━━━━━━━━━━━━"
            return Response(content=report, media_type="text/plain")

        elif body.lower() == "!undo":
            db = SessionLocal()
            last_log = db.query(CalorieLog).filter(CalorieLog.user_phone == sender).order_by(CalorieLog.timestamp.desc()).first()
            if not last_log:
                db.close()
                return Response(content="⚠️ No recent entries found to undo.", media_type="text/plain")
            
            food_name = last_log.food_item
            food_cals = last_log.calories
            db.delete(last_log)
            db.commit()
            db.close()
            
            daily_metrics = get_daily_metrics(sender)
            daily_total = daily_metrics["calories"]
            food_streak = calculate_streak(sender, exercise_only=False)
            
            target = 1200
            if daily_total <= target:
                status_text = f"🟢 *REMAINING:* ```{target - daily_total} kcal```"
            else:
                status_text = f"🔴 *OVERFLOW:* ```{daily_total - target} kcal```"
                
            undo_message = (
                f"━━━━━━━━━━━━━━━\n"
                f"↩️ *UNDO SUCCESSFUL*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Deleted: {last_log.food_item[:20]} ({last_log.calories} kcal)\n\n"
                f"📊 *NEW DAILY TOTAL:* ```{daily_total} / {target} kcal```\n"
                f"{status_text}\n"
                f"🍕 *FOOD STREAK:* ```{food_streak} days```\n"
                f"━━━━━━━━━━━━━━━"
            )
            return Response(content=undo_message, media_type="text/plain")

        elif body.lower() == "!commands" or body.lower() == "!command":
            help_text = (
                "📜 *AVAILABLE COMMANDS*\n"
                "━━━━━━━━━━━━━━━\n"
                "• *!today* - Today's progress\n"
                "• *!summary* - Weekly report\n"
                "• *!undo* - Delete last entry\n"
                "• *!dayhistory* - Clear today's history\n"
                "• *!delhistory* - Reset entire history\n"
                "• *!commands* - Get All the commands\n"
                "━━━━━━━━━━━━━━━\n"
                "💡 *Tip:* Send a food photo or text to log it!"
            )
            return Response(content=help_text, media_type="text/plain")

        elif body.lower() == "!today":
            metrics = get_daily_metrics(sender)
            food_streak = calculate_streak(sender, exercise_only=False)
            ex_streak = calculate_streak(sender, exercise_only=True)
            t_cal, t_prot, t_carb, t_fat = 1200, 75, 150, 34
            daily_food = metrics["calories"]
            bonus_cals = metrics["burned"]
            if daily_food <= t_cal:
                remaining = t_cal - daily_food
                status_text = f"🟢 *REMAINING:* ```{remaining} kcal```"
            else:
                excess = daily_food - t_cal
                net_excess = excess - bonus_cals
                if net_excess <= 0:
                    status_text = f"🟢 *OVERFLOW COVERED BY BONUS!*"
                else:
                    status_text = f"🔴 *OVERFLOW:* ```{net_excess} kcal```"
            today_report = (
                f"━━━━━━━━━━━━━━━\n"
                f"📅 *TODAY'S SUMMARY*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥩 *MACROS TD:* \n"
                f"P: {metrics['protein']}/{t_prot}g | C: {metrics['carbs']}/{t_carb}g | F: {metrics['fats']}/{t_fat}g\n\n"
                f"📊 *DAILY TOTAL STACK:* \n"
                f"Consumed: {daily_food} / {t_cal} kcal\n"
                f"🔥 Bonus: {bonus_cals} kcal burned\n\n"
                f"{status_text}\n"
                f"🌟 *STREAK:* \n"
                f"🍕 Food: {food_streak} days | 💪 Exercise: {ex_streak} days\n"
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

        # 2. RUN ANALYSIS (IMAGE VS TEXT)
        if image_b64:
            print(f"📸 Combined Entry: Photo + '{body}'")
            ai_response = analyze_image_with_ai(image_b64, user_note=body)
            log_text = f"📸 Photo ({body})" if body else "📸 Photo Entry"
        else:
            print(f"✍️ TEXT DETECTED: Analyzing '{body}'...")
            ai_response = analyze_food_with_ai(body)
            log_text = body

        # 3. EXTRACT CALORIES & MACROS (Regex)
        is_exercise = 1 if "Log Type: Exercise" in ai_response else 0

        # Extract Calories
        matches_cal = re.findall(r"Total Estimated: (\d+)", ai_response, re.IGNORECASE)
        calories_value = int(matches_cal[-1]) if matches_cal else 0

        # Extract Macros (Protein: [g], Carbs: [g], Fats: [g])
        p_match = re.search(r"Protein: (\d+)", ai_response, re.IGNORECASE)
        c_match = re.search(r"Carbs: (\d+)", ai_response, re.IGNORECASE)
        f_match = re.search(r"Fats: (\d+)", ai_response, re.IGNORECASE)

        p_val = int(p_match.group(1)) if p_match or is_exercise == 1 else 0
        c_val = int(c_match.group(1)) if c_match or is_exercise == 1 else 0
        f_val = int(f_match.group(1)) if f_match or is_exercise == 1 else 0
            
        print(f"DEBUG: Extracted {calories_value}kcal, P:{p_val}, C:{c_val}, F:{f_val}, Exercise:{is_exercise}")
        
        # 4. SAVE TO DATABASE
        db = SessionLocal()
        db.add(CalorieLog(
            user_phone=sender, 
            food_item=log_text, 
            calories=calories_value,
            protein=p_val,
            carbs=c_val,
            fats=f_val,
            is_exercise=is_exercise
        ))
        db.commit()
        db.close()

        # 5. FINAL FORMATTING
        metrics = get_daily_metrics(sender)
        food_streak = calculate_streak(sender, exercise_only=False)
        ex_streak = calculate_streak(sender, exercise_only=True)
        
        # Targets
        t_cal, t_prot, t_carb, t_fat = 1200, 75, 150, 34
        
        daily_food = metrics["calories"]
        bonus_cals = metrics["burned"]
        
        if daily_food <= t_cal:
            remaining = t_cal - daily_food
            status_text = f"🟢 *REMAINING:* ```{remaining} kcal```"
        else:
            excess = daily_food - t_cal
            net_excess = excess - bonus_cals
            if net_excess <= 0:
                status_text = f"🟢 *OVERFLOW COVERED BY BONUS!*"
            else:
                status_text = f"🔴 *OVERFLOW:* ```{net_excess} kcal```"

        # Construct Sections
        header = f"⚡ *EXERCISE REPORT*" if is_exercise else f"🍽️ *NUTRITION REPORT*"
        
        # Macro Section (Hide for exercise)
        macro_section = ""
        if not is_exercise:
            macro_section = (
                f"🥩 *MACROS TD:* \n"
                f"P: {metrics['protein']}/{t_prot}g | C: {metrics['carbs']}/{t_carb}g | F: {metrics['fats']}/{t_fat}g\n\n"
            )

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
            f"🌟 *STREAK:* \n"
            f"🍕 Food: {food_streak} days | 💪 Exercise: {ex_streak} days\n"
            f"━━━━━━━━━━━━━━━"
        )
        return Response(content=final_message, media_type="text/plain")

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        return Response(content=f"❌ Error: {str(e)}", media_type="text/plain")