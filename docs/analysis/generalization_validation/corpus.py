"""Synthetic multi-domain query corpus for generalization validation.

Purpose: stress-test candidate non-LLM query-refinement techniques on domains
OUTSIDE the technical/code corpus already mined from search_events.duckdb
(medical, legal, shopping, local/geo, travel, recipes, finance, trivia,
navigational/brand), so conclusions are not overfit to developer queries.

Every row carries ground truth labels needed to compute real accuracy
(not eyeballed): true_lang, true_intent, typo_of (if applicable),
glued_segments (if applicable). This is a static, hand-curated fixture —
not sampled from the production DB — precisely because we need known-correct
labels to score technique outputs against.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryCase:
    text: str
    domain: str
    true_lang: str = "en"
    true_intent: str = "informational"  # informational|navigational|transactional|commercial|local|comparative
    typo_of: str | None = None  # corrected spelling, if text contains an injected typo
    glued_segments: tuple[str, ...] | None = None  # expected split, if text has glued words


CORPUS: list[QueryCase] = [
    # ---------------- medical / health ----------------
    QueryCase("symptoms of type 2 diabetes in adults", "medical", true_intent="informational"),
    QueryCase("amoxicilin dosage for adults", "medical", typo_of="amoxicillin dosage for adults", true_intent="informational"),
    QueryCase("wieght loss diet plan for beginners", "medical", typo_of="weight loss diet plan for beginners", true_intent="informational"),
    QueryCase("best cardiologist near me", "medical", true_intent="local"),
    QueryCase("ibuprofen vs acetaminophen for fever", "medical", true_intent="comparative"),
    QueryCase("healthyweightlossdiet", "medical", glued_segments=("healthy", "weight", "loss", "diet"), true_intent="informational"),
    QueryCase("mayo clinic appointment scheduling", "medical", true_intent="navigational"),
    QueryCase("affordablehealthinsurance", "medical", glued_segments=("affordable", "health", "insurance"), true_intent="commercial"),
    QueryCase("how contagious is the flu after symptoms start", "medical", true_intent="informational"),
    QueryCase("buy blood pressure monitor online", "medical", true_intent="transactional"),
    # ---------------- legal ----------------
    QueryCase("how to file for divorce in california", "legal", true_intent="informational"),
    QueryCase("seperate legal agreement for business partners", "legal", typo_of="separate legal agreement for business partners", true_intent="informational"),
    QueryCase("toplawyersinnewyork", "legal", glued_segments=("top", "lawyers", "in", "new", "york"), true_intent="commercial"),
    QueryCase("small claims court filing fee", "legal", true_intent="informational"),
    QueryCase("hire immigration attorney near me", "legal", true_intent="transactional"),
    QueryCase("nda template vs mutual nda agreement", "legal", true_intent="comparative"),
    QueryCase("uscis official website", "legal", true_intent="navigational"),
    QueryCase("statute of limitations for breech of contract", "legal", typo_of="statute of limitations for breach of contract", true_intent="informational"),
    QueryCase("best patent lawyers for startups review", "legal", true_intent="commercial"),
    QueryCase("power of attorney form download", "legal", true_intent="transactional"),
    # ---------------- shopping / e-commerce ----------------
    QueryCase("bestlaptop2026", "shopping", glued_segments=("best", "laptop", "2026"), true_intent="commercial"),
    QueryCase("nikee air max size 10", "shopping", typo_of="nike air max size 10", true_intent="transactional"),
    QueryCase("buyusedcarnearme", "shopping", glued_segments=("buy", "used", "car", "near", "me"), true_intent="transactional"),
    QueryCase("bestcreditcardsforcashback", "shopping", glued_segments=("best", "credit", "cards", "for", "cash", "back"), true_intent="commercial"),
    QueryCase("iphone 15 vs samsung galaxy s24 comparison", "shopping", true_intent="comparative"),
    QueryCase("amazon prime membership signup", "shopping", true_intent="transactional"),
    QueryCase("cheapest 4k tv deals this week", "shopping", true_intent="commercial"),
    QueryCase("track my usp package delivery", "shopping", typo_of="track my ups package delivery", true_intent="navigational"),
    QueryCase("return policy for target online orders", "shopping", true_intent="informational"),
    QueryCase("wireless earbuds under 50 dollars", "shopping", true_intent="commercial"),
    # ---------------- local / geo intent ----------------
    QueryCase("nearbycoffeeshops", "local", glued_segments=("nearby", "coffee", "shops"), true_intent="local"),
    QueryCase("resturants near me open now", "local", typo_of="restaurants near me open now", true_intent="local"),
    QueryCase("24 hour pharmacy open near me", "local", true_intent="local"),
    QueryCase("best pizza in chicago", "local", true_intent="commercial"),
    QueryCase("nearest gas station with diesel", "local", true_intent="local"),
    QueryCase("dentist accepting new patients near me", "local", true_intent="local"),
    QueryCase("weather forecast this weekend", "local", true_intent="informational"),
    QueryCase("public parking downtown seattle", "local", true_intent="informational"),
    QueryCase("hair salon walk in near me", "local", true_intent="local"),
    QueryCase("timezone difference new york to london", "local", true_intent="informational"),
    # ---------------- travel ----------------
    QueryCase("cheapflightstoparis", "travel", glued_segments=("cheap", "flights", "to", "paris"), true_intent="transactional"),
    QueryCase("flght delay compensation eu rules", "travel", typo_of="flight delay compensation eu rules", true_intent="informational"),
    QueryCase("best time to visit japan for cherry blossoms", "travel", true_intent="informational"),
    QueryCase("booking.com official site", "travel", true_intent="navigational"),
    QueryCase("hotel vs airbnb for family trip cost comparison", "travel", true_intent="comparative"),
    QueryCase("passport renewal appointment", "travel", true_intent="transactional"),
    QueryCase("visa requirements for us citizens visiting vietnam", "travel", true_intent="informational"),
    QueryCase("top rated all inclusive resorts cancun", "travel", true_intent="commercial"),
    QueryCase("carry on luggage size limits delta airlines", "travel", true_intent="informational"),
    QueryCase("book rental car denver airport", "travel", true_intent="transactional"),
    # ---------------- recipes / cooking ----------------
    QueryCase("quickpastarecipe", "recipes", glued_segments=("quick", "pasta", "recipe"), true_intent="informational"),
    QueryCase("chiken tikka masala recipe", "recipes", typo_of="chicken tikka masala recipe", true_intent="informational"),
    QueryCase("how to remove wine stain from carpet", "recipes", true_intent="informational"),
    QueryCase("substitute for buttermilk in baking", "recipes", true_intent="informational"),
    QueryCase("air fryer vs oven for roasting vegetables", "recipes", true_intent="comparative"),
    QueryCase("gordon ramsay official youtube channel", "recipes", true_intent="navigational"),
    QueryCase("best instant pot recipes for beginners", "recipes", true_intent="commercial"),
    QueryCase("gluten free bread recipe easy", "recipes", true_intent="informational"),
    QueryCase("buy sourdough starter kit online", "recipes", true_intent="transactional"),
    QueryCase("how long to boil eggs for hard boiled", "recipes", true_intent="informational"),
    # ---------------- finance ----------------
    QueryCase("mortgage refincance rates today", "finance", typo_of="mortgage refinance rates today", true_intent="commercial"),
    QueryCase("roth ira vs traditional ira tax difference", "finance", true_intent="comparative"),
    QueryCase("how to build credit score from scratch", "finance", true_intent="informational"),
    QueryCase("open a high yield savings account", "finance", true_intent="transactional"),
    QueryCase("nasdaq composite index today", "finance", true_intent="navigational"),
    QueryCase("best budgeting apps 2026 review", "finance", true_intent="commercial"),
    QueryCase("capital gains tax rate for stocks held one year", "finance", true_intent="informational"),
    QueryCase("student loan forgiveness eligibility", "finance", true_intent="informational"),
    QueryCase("transfer money internationally lowest fees", "finance", true_intent="commercial"),
    QueryCase("file taxes online turbotax", "finance", true_intent="navigational"),
    # ---------------- general trivia / factual ----------------
    QueryCase("who won the world cup in 2022", "trivia", true_intent="informational"),
    QueryCase("population of france 2026", "trivia", true_intent="informational"),
    QueryCase("tallest mountain in the world", "trivia", true_intent="informational"),
    QueryCase("when was the eiffel tower built", "trivia", true_intent="informational"),
    QueryCase("how many moons does jupiter have", "trivia", true_intent="informational"),
    QueryCase("speed of light in vaccuum", "trivia", typo_of="speed of light in vacuum", true_intent="informational"),
    QueryCase("wikipedia official site", "trivia", true_intent="navigational"),
    QueryCase("difference between alligator and crocodile", "trivia", true_intent="comparative"),
    QueryCase("who invented the telephone", "trivia", true_intent="informational"),
    QueryCase("largest country by land area", "trivia", true_intent="informational"),
    # ---------------- navigational / brand ----------------
    QueryCase("facebook login", "navigational", true_intent="navigational"),
    QueryCase("gmail sign in", "navigational", true_intent="navigational"),
    QueryCase("netflix account settings", "navigational", true_intent="navigational"),
    QueryCase("chase bank customer service number", "navigational", true_intent="navigational"),
    QueryCase("linkedin profile update", "navigational", true_intent="navigational"),
    QueryCase("spotify premium login", "navigational", true_intent="navigational"),
    QueryCase("irs.gov forms", "navigational", true_intent="navigational"),
    QueryCase("paypal customer support chat", "navigational", true_intent="navigational"),
    QueryCase("costco membership renewal", "navigational", true_intent="navigational"),
    QueryCase("adobe acrobat download", "navigational", true_intent="navigational"),
    # ---------------- technical (small control set, independent of DB corpus) ----------------
    QueryCase("python asyncio vs threading for io bound tasks", "technical", true_intent="comparative"),
    QueryCase("duckdb read only connection example", "technical", true_intent="informational"),
    QueryCase("how to fix connection refused error postgres", "technical", true_intent="informational"),
    QueryCase("github actions official documentation", "technical", true_intent="navigational"),
    QueryCase("best python ide for data science 2026", "technical", true_intent="commercial"),
    # ---------------- non-English (language-ID stress set) ----------------
    QueryCase("najlepsza restauracja w warszawie", "local_pl", true_lang="pl", true_intent="local"),
    QueryCase("jak przygotowac bigos krok po kroku", "recipes_pl", true_lang="pl", true_intent="informational"),
    QueryCase("objawy grypy u doroslych", "medical_pl", true_lang="pl", true_intent="informational"),
    QueryCase("mejor restaurante cerca de mi", "local_es", true_lang="es", true_intent="local"),
    QueryCase("como bajar de peso rapido y sano", "medical_es", true_lang="es", true_intent="informational"),
    QueryCase("precio del bitcoin hoy", "finance_es", true_lang="es", true_intent="informational"),
    QueryCase("beste restaurants in der nahe", "local_de", true_lang="de", true_intent="local"),
    QueryCase("wie funktioniert eine waermepumpe", "technical_de", true_lang="de", true_intent="informational"),
    QueryCase("meilleur restaurant pas cher paris", "local_fr", true_lang="fr", true_intent="local"),
    QueryCase("comment reduire les frais bancaires", "finance_fr", true_lang="fr", true_intent="informational"),
]


def domains() -> list[str]:
    return sorted({c.domain for c in CORPUS})


if __name__ == "__main__":
    print(f"Total cases: {len(CORPUS)} across {len(domains())} domain buckets")
    for d in domains():
        n = sum(1 for c in CORPUS if c.domain == d)
        print(f"  {d}: {n}")
