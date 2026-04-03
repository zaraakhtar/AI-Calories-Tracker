# 🥗 AI WhatsApp Calorie Tracker

A smart, automated fitness assistant that turns your WhatsApp messages (text and images) into accurate calorie and macro logs. 

Built with **FastAPI**, **Node.js**, and **Llama 3/4 (Groq)**.

---

## ✨ Features
- **📸 Vision Logging**: Send a photo of your meal, and the AI identifies the items and estimates macros.
- **✍️ Text Logging**: Simply type what you ate (e.g., "3 eggs and a piece of toast").
- **💪 Exercise Tracking**: Log workouts to earn "Bonus Calories" that offset your daily intake.
- **📊 Interactive Reports**:
    - `!today`: Quick summary of calories, macros, and streaks.
    - `!summary`: Weekly breakdown of your progress.
    - `!undo`: Instantly delete the last entry if you made a mistake.
- **🔥 Streak System**: Maintain separate streaks for both nutrition and exercise.
- **🔒 Private Bot**: Filtered to work specifically within your chosen WhatsApp group.

---

## 🛠️ Tech Stack
- **AI Brain**: [Groq](https://groq.com/) (Llama-3.3-70B & Llama-4-Scout)
- **Backend**: Python 3.10+, FastAPI, SQLAlchemy, SQLite
- **WhatsApp Bridge**: Node.js, `whatsapp-web.js`

---

## 🚀 Getting Started

### 1. Prerequisites
- Python installed
- Node.js installed
- A [Groq API Key](https://console.groq.com/)

### 2. Configuration
Create a `.env` file in the root directory:
```env
GROQ_API_KEY=your_key_here
TARGET_GROUP_ID=your_whatsapp_group_id@g.us
```

### 3. Installation

**Python Backend:**
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

**Node.js Bridge:**
```bash
npm install
```

### 4. Running the App
You need **two** terminals running simultaneously:

**Terminal 1 (Python):**
```bash
uvicorn main:app --reload
```

**Terminal 2 (WhatsApp):**
```bash
node whatsapp_bridge.js
```
*Note: On first run, scan the QR code in Terminal 2 with your phone's WhatsApp.*

---

## 📜 Commands Reference
| Command | Action |
| :--- | :--- |
| `!today` | Shows current calories/macros and streaks |
| `!summary` | Last 7 days progress report |
| `!undo` | Deletes the last logged entry |
| `!commands` | List all available commands |
| `!dayhistory` | Clears all logs for today |
| `!delhistory` | Wipes your entire database history |

---

## 🏗️ Project Structure
- `main.py`: The FastAPI server handling AI analysis and logic.
- `whatsapp_bridge.js`: Connects to WhatsApp and forwards messages to the backend.
- `database.py`: SQLAlchemy models and SQLite connection.
- `calories.db`: Local database store.

---
*Created with ❤️ by Zara Akhtar*
