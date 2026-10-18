"""Hinglish call-transcript scam detector.

Synthetic transcripts for training (scam vs. legit) and a char n-gram classifier that is
robust to Hinglish spelling variation ("paise"/"paisa"/"pese").
"""
import random
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

AGENCY = ["CBI", "cyber crime", "Mumbai police", "customs", "ED", "narcotics department", "TRAI", "RBI"]
ITEM = ["drugs", "fake passport", "5 ATM card", "MDMA", "illegal sim card", "hawala ka paisa"]
BANK = ["SBI", "HDFC", "ICICI", "Axis bank", "Paytm", "PhonePe"]

SCAM = {
    "digital_arrest": [
        "main {agency} officer bol raha hoon, aapke aadhaar se ek parcel mein {item} mila hai, aap digital arrest mein ho",
        "aapke naam pe FIR darj hui hai, video call band mat karna, kisi ko batana mat warna arrest ho jayega",
        "money laundering case mein aapka account use hua hai, verification ke liye saara paisa RBI safe account mein transfer karo",
        "{agency} se call hai, aapka number illegal activity mein mila, abhi ke abhi {amount} jama karo warna warrant nikal jayega",
        "ye confidential investigation hai, family ko mat batana, skype pe bane raho aur jo account bataun usme paise bhejo",
        "sir aapka parcel customs ne pakda hai usme {item} hai, case settle karne ke liye fine bharna padega UPI se",
    ],
    "kyc_scam": [
        "main {bank} se bol raha hoon, aapka KYC expire ho gaya hai, account block ho jayega, abhi OTP batao",
        "aapke account mein suspicious login hua hai, safe karne ke liye ek link bheja hai usme UPI pin daalo",
        "aapka electricity connection aaj raat kat jayega, bill update karne ke liye is number pe {amount} bhejo",
        "aapko {amount} ka refund aana hai, collect request accept karo aur pin daalo to paise aa jayenge",
        "sir aapka credit card limit badhana hai, anydesk app download karo screen share karo",
        "aapka {bank} account freeze ho gaya hai, unfreeze karne ke liye verification amount transfer karna hoga",
    ],
}

LEGIT = [
    "hello beta khana kha liya, shaam ko ghar aate waqt doodh le aana",
    "sir aapka order out for delivery hai, ghar pe koi hoga kya, cash on delivery hai",
    "{bank} customer care, aapki complaint register ho gayi hai, kabhi bhi OTP ya pin kisi ko mat batana",
    "bhai kal ka match dekhne chalega, tickets maine book kar liye, paise baad mein de dena",
    "madam aapka cylinder book ho gaya hai, kal subah delivery aayegi",
    "aapka {bank} credit card bill due hai, app se payment kar sakte hain, dhanyavaad",
    "haan bhai rent bhej diya hai UPI se, check kar lena",
    "sir main plumber bol raha hoon, kal 10 baje aa jaunga, advance {amount} bhej dena",
    "mummy ne bola tha ki chacha ji ko paise bhej do, number whatsapp kar diya hai",
    "doctor ka appointment kal 4 baje hai, report saath le aana",
    "{bank} fraud team se call hai, aapke card se {amount} ka transaction hua, kya aapne kiya tha, nahi kiya to card block kar dete hain",
    "police station se bol raha hoon, aapki shikayat par FIR darj ho gayi hai, kal aakar copy le jaana",
    "aapka KYC update pending hai, nazdeeki branch mein aadhaar le kar aaiye, phone pe koi details mat dena",
    "courier wala bol raha hoon parcel ka address confirm karna tha, OTP aayega delivery ke time",
    "beta urgent hai hospital mein hoon, {amount} abhi bhej do, baad mein baat karta hoon",
    "office meeting shift ho gayi hai, zoom link mail kar diya hai",
    "aapka {bank} account ka statement email pe bhej diya gaya hai, koi sawaal ho to branch aaiye",
]


def _fill(t, r):
    t = t.format(agency=r.choice(AGENCY), item=r.choice(ITEM), bank=r.choice(BANK),
                 amount=f"{r.choice([2, 5, 10, 25, 50, 99])} hazaar")
    words = t.split()
    words = [w for w in words if r.random() > 0.2]  # ASR drops words
    swaps = {"paise": ["paisa", "pese", "paisay"], "karo": ["kro", "karo"], "hai": ["he", "hai"], "nahi": ["nhi"]}
    return " ".join(r.choice(swaps[w]) if w in swaps else w for w in words)


def scam_transcript(r, kind=None):
    kind = kind or r.choice(list(SCAM))
    a, b = r.sample(SCAM[kind], 2)
    if r.random() < 0.3:  # scammers open with small talk
        b = r.choice(LEGIT)
    return _fill(a + ". " + b, r)


def legit_transcript(r):
    a, b = r.sample(LEGIT, 2)
    return _fill(a + ". " + b, r)


def normalize(t):
    return re.sub(r"\s+", " ", t.lower())


def train_call_model(seed=1, n=4000):
    r = random.Random(seed)
    X, y = [], []
    for _ in range(n):
        if r.random() < 0.5:
            X.append(scam_transcript(r)); y.append(1)
        else:
            X.append(legit_transcript(r)); y.append(0)
    model = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), preprocessor=normalize,
                                          min_df=2, sublinear_tf=True),
                          LogisticRegression(C=2.0, max_iter=2000))
    model.fit(X, y)
    return model
