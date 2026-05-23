import random
from datetime import date

from fastapi import APIRouter, Depends
from supabase import Client

from auth import get_current_user
from database import get_db
from schemas import ok

router = APIRouter()

_QUOTES = [
    {"text": "The only bad workout is the one that didn't happen.", "author": "Unknown"},
    {"text": "Push yourself because no one else is going to do it for you.", "author": "Unknown"},
    {"text": "Your body can stand almost anything. It's your mind that you have to convince.", "author": "Unknown"},
    {"text": "Success starts with self-discipline.", "author": "Unknown"},
    {"text": "Don't stop when you're tired. Stop when you're done.", "author": "Unknown"},
    {"text": "Every step forward is a step away from where you used to be.", "author": "Unknown"},
    {"text": "It never gets easier. You just get stronger.", "author": "Unknown"},
    {"text": "Run when you can, walk if you have to, crawl if you must; just never give up.", "author": "Dean Karnazes"},
    {"text": "The miracle isn't that I finished. The miracle is that I had the courage to start.", "author": "John Bingham"},
    {"text": "Running is the greatest metaphor for life — you get out of it what you put into it.", "author": "Oprah Winfrey"},
    {"text": "Pain is temporary. Quitting lasts forever.", "author": "Lance Armstrong"},
    {"text": "The faster you run, the sooner you're done.", "author": "Unknown"},
    {"text": "Your speed doesn't matter. Forward is forward.", "author": "Unknown"},
    {"text": "Believe you can and you're halfway there.", "author": "Theodore Roosevelt"},
    {"text": "It's not about having time. It's about making time.", "author": "Unknown"},
    {"text": "Champions aren't made in gyms. Champions are made from something they have deep inside them.", "author": "Muhammad Ali"},
    {"text": "What seems impossible today will one day become your warm-up.", "author": "Unknown"},
    {"text": "Don't wish for it. Work for it.", "author": "Unknown"},
    {"text": "Sweat is just fat crying.", "author": "Unknown"},
    {"text": "The hardest step is the one out the door.", "author": "Unknown"},
]

_HEALTH_TIPS = [
    {"category": "NUTRITION", "tip": "Drink water before you feel thirsty — thirst is already a sign of dehydration."},
    {"category": "NUTRITION", "tip": "Eat a banana 30 minutes before your run for quick natural energy."},
    {"category": "NUTRITION", "tip": "Post-run: eat protein + carbs within 30 minutes to speed up muscle recovery."},
    {"category": "NUTRITION", "tip": "Avoid sugary drinks during long runs — opt for electrolyte water instead."},
    {"category": "NUTRITION", "tip": "Add eggs to your breakfast — they provide complete protein for muscle repair."},
    {"category": "NUTRITION", "tip": "Oats are a runner's best friend: slow-release energy to fuel long sessions."},
    {"category": "NUTRITION", "tip": "Dark chocolate in moderation improves blood flow and reduces muscle soreness."},
    {"category": "RECOVERY", "tip": "Sleep 7-9 hours. Most muscle repair happens while you sleep."},
    {"category": "RECOVERY", "tip": "Foam roll your calves and IT band after every long run to prevent tightness."},
    {"category": "RECOVERY", "tip": "Ice bath or cold shower after intense runs reduces inflammation significantly."},
    {"category": "RECOVERY", "tip": "Take at least one full rest day per week. Rest is training too."},
    {"category": "RECOVERY", "tip": "Stretch your hip flexors daily — tight hips are the #1 cause of runner injuries."},
    {"category": "TRAINING", "tip": "Run 80% of your miles at conversational pace — easy running builds your aerobic base."},
    {"category": "TRAINING", "tip": "Increase your weekly mileage by no more than 10% to avoid overuse injuries."},
    {"category": "TRAINING", "tip": "Strength training twice a week makes you a faster, injury-resistant runner."},
    {"category": "TRAINING", "tip": "Vary your terrain — trail running builds ankle stability and mental toughness."},
    {"category": "TRAINING", "tip": "Breathing through your nose on easy runs increases stamina over time."},
    {"category": "MENTAL", "tip": "Break long runs into smaller milestones — celebrate each one mentally."},
    {"category": "MENTAL", "tip": "Create a hype playlist. Music can boost running performance by up to 15%."},
    {"category": "MENTAL", "tip": "Visualise the finish before you start — mental rehearsal improves performance."},
    {"category": "MENTAL", "tip": "Track your progress weekly. Seeing improvement is the strongest motivator."},
    {"category": "WORKLIFE", "tip": "A 10-minute morning walk increases focus and reduces stress all day."},
    {"category": "WORKLIFE", "tip": "Stand up and move for 5 minutes every hour of desk work to boost energy."},
    {"category": "WORKLIFE", "tip": "Lunchtime runs are proven to increase afternoon productivity and creativity."},
    {"category": "WORKLIFE", "tip": "Exercise and nature (RunRealm's map feature) together are a double stress-buster."},
]


@router.get("/quote")
def daily_quote(user=Depends(get_current_user)):
    # Deterministic by date so the same quote shows all day, rotates daily
    seed = int(date.today().strftime("%Y%m%d"))
    random.seed(seed)
    quote = random.choice(_QUOTES)
    return ok(quote)


@router.get("/quote/random")
def random_quote(user=Depends(get_current_user)):
    return ok(random.choice(_QUOTES))


@router.get("/tip")
def daily_tip(user=Depends(get_current_user)):
    seed = int(date.today().strftime("%Y%m%d")) + 1
    random.seed(seed)
    tip = random.choice(_HEALTH_TIPS)
    return ok(tip)


@router.get("/tip/random")
def random_tip(
    category: str | None = None,
    user=Depends(get_current_user),
):
    pool = _HEALTH_TIPS
    if category:
        pool = [t for t in _HEALTH_TIPS if t["category"].upper() == category.upper()]
    if not pool:
        pool = _HEALTH_TIPS
    return ok(random.choice(pool))


@router.get("/feed")
def content_feed(user=Depends(get_current_user)):
    seed = int(date.today().strftime("%Y%m%d"))
    random.seed(seed)
    return ok({
        "quote": random.choice(_QUOTES),
        "tip": random.choice(_HEALTH_TIPS),
    })
