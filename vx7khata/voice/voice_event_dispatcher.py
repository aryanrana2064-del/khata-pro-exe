"""What the assistant is allowed to say, and when.

Every event here maps to a place in the app where the operation is already known to have finished; there are
deliberately no events for bills, stock, payment modes, staff or licences because this application has no such
feature (see README: "no inventory, stock or billing/POS"). Announcing something that never happened would be
worse than announcing less.

Templates are short on purpose - they are read aloud, not displayed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .voice_phrases import amount_words
from .voice_settings import LANGUAGE_ENGLISH, LANGUAGE_HINDI, LANGUAGE_HINGLISH

PRIORITY_LOW = 0
PRIORITY_NORMAL = 1
PRIORITY_HIGH = 2


@dataclass(frozen=True)
class VoiceEvent:
    name: str
    category: str
    label: str                       # what the settings panel calls it
    priority: int = PRIORITY_NORMAL
    templates: dict = None           # language -> sentence with {placeholders}
    spoken: tuple = ()               # value names rendered as words rather than digits

    def template(self, language: str) -> str:
        return (self.templates or {}).get(language, (self.templates or {}).get(LANGUAGE_HINGLISH, ""))


def _e(name, category, label, en, hi, hinglish, priority=PRIORITY_NORMAL,
       spoken=("amount", "balance")) -> VoiceEvent:
    return VoiceEvent(name, category, label, priority,
                      {LANGUAGE_ENGLISH: en, LANGUAGE_HINDI: hi, LANGUAGE_HINGLISH: hinglish}, spoken)


EVENT_CATALOGUE: tuple[VoiceEvent, ...] = (
    # ---- application
    _e("app_ready", "Application", "Application ready",
       "Khata Pro is ready.", "खाता प्रो तैयार है।", "Khata Pro ready hai."),
    _e("app_closing", "Application", "Application closing",
       "Khata Pro closing. Data saved.", "खाता प्रो बंद हो रहा है। डेटा सुरक्षित है।",
       "Khata Pro band ho raha hai. Data safe hai.", PRIORITY_LOW),
    _e("db_opened", "Application", "Data file opened",
       "Data file connected.", "डेटा फाइल जुड़ गई है।", "Data file jud gayi hai.", PRIORITY_LOW),
    _e("db_error", "Application", "Data file error",
       "Warning. The data file could not be opened.", "चेतावनी। डेटा फाइल नहीं खुल सकी।",
       "Chetawani. Data file nahi khul saki.", PRIORITY_HIGH),
    _e("data_saved", "Application", "Settings saved",
       "Settings saved.", "सेटिंग्स सुरक्षित हो गईं।", "Settings save ho gayi.", PRIORITY_LOW),
    _e("backup_done", "Application", "Backup completed",
       "Backup completed successfully.", "बैकअप पूरी तरह हो गया है।", "Backup pura ho gaya hai."),
    _e("backup_failed", "Application", "Backup failed",
       "Warning. Backup failed.", "चेतावनी। बैकअप नहीं हो सका।", "Chetawani. Backup nahi ho saka.", PRIORITY_HIGH),
    _e("restore_done", "Application", "Backup restored",
       "Backup restored successfully.", "बैकअप बहाल कर दिया गया है।", "Backup restore ho gaya hai."),
    _e("restore_failed", "Application", "Backup restore failed",
       "Warning. The backup could not be restored. Your current data is unchanged.",
       "चेतावनी। बैकअप बहाल नहीं हो सका। आपका वर्तमान डेटा वैसा ही है।",
       "Chetawani. Backup restore nahi ho saka. Aapka current data waisa hi hai.", PRIORITY_HIGH),
    # ---- ledger entries (this app's equivalent of billing)
    _e("entry_added", "Ledger", "New entry saved",
       "New entry saved. {type} {amount}.", "नई प्रविधि सुरक्षित। {type} {amount}।",
       "Naya entry save ho gaya. {type} {amount}."),
    _e("entry_updated", "Ledger", "Entry updated",
       "Entry updated. {amount}.", "प्रविधि बदल दी गई है। {amount}।", "Entry update ho gayi. {amount}."),
    _e("entry_deleted", "Ledger", "Entry deleted",
       "Entry deleted.", "प्रविधि हटा दी गई है।", "Entry hata di gayi hai."),
    _e("entry_duplicate", "Ledger", "Duplicate entry blocked",
       "Possible duplicate entry. Not saved.", "संभव नकल प्रविधि। सुरक्षित नहीं किया।",
       "Duplicate entry ho sakti hai. Save nahi kiya.", PRIORITY_HIGH),
    _e("entry_over_limit", "Ledger", "Credit limit exceeded",
       "Warning. {customer} is over the credit limit. Balance {balance}.",
       "चेतावनी। {customer} क्रेडिट सीमा से अधिक हैं। शेष {balance}।",
       "Chetawani. {customer} credit limit se bahar hain. Balance {balance}.", PRIORITY_HIGH),
    _e("payment_received", "Ledger", "Payment received",
       "Payment received. {amount}. Balance now {balance}.", "भुगतान मिल गया। {amount}। अब शेष {balance}।",
       "Payment mil gaya. {amount}. Ab balance {balance}."),
    _e("daily_queued", "Ledger", "Daily Entry rows queued",
       "{count} added to the waiting list.", "{count} प्रतीक्षा सूची में जोड़ी गईं।",
       "{count} waiting list me add ho gayi."),
    _e("daily_posted", "Ledger", "Daily Entry posted to ledger",
       "{count} saved into the ledger.", "{count} खाता में जोड़ दी गईं।",
       "{count} ledger me save ho gayi."),
    # ---- customers
    _e("customer_added", "Customers", "Customer added",
       "New customer added. {customer}.", "नया ग्राहक जोड़ा गया। {customer}।", "Naya customer add ho gaya. {customer}."),
    _e("customer_updated", "Customers", "Customer updated",
       "Customer updated. {customer}.", "ग्राहक की जानकारी बदली। {customer}।", "Customer update ho gaya. {customer}."),
    _e("customer_deleted", "Customers", "Customer deleted",
       "Customer deleted.", "ग्राहक हटा दिया गया।", "Customer hata diya gaya."),
    _e("balance_updated", "Customers", "Outstanding balance changed",
       "{customer} balance is now {balance}.", "{customer} का शेष अब {balance} है।",
       "{customer} ka balance ab {balance} hai."),
    # ---- reports and printing
    _e("export_done", "Reports", "Report exported",
       "Report saved to file.", "रिपोर्ट फाइल में सुरक्षित हो गई।", "Report file me save ho gayi."),
    _e("export_failed", "Reports", "Report export failed",
       "Warning. The report could not be saved.", "चेतावनी। रिपोर्ट सुरक्षित नहीं हो सकी।",
       "Chetawani. Report save nahi ho saki.", PRIORITY_HIGH),
    _e("print_sent", "Reports", "Sent to printer",
       "Sent to printer.", "प्रिंटर को भेज दिया गया।", "Printer ko bhej diya gaya."),
    _e("print_failed", "Reports", "Printing failed",
       "Warning. Printing failed.", "चेतावनी। प्रिंटिंग नहीं हो सकी।",
       "Chetawani. Printing fail ho gayi.", PRIORITY_HIGH),
    # ---- security
    _e("unlocked", "Security", "Correct PIN",
       "Welcome back.", "वापसी पर नमस्ते।", "Welcome back.", PRIORITY_LOW),
    _e("pin_wrong", "Security", "Wrong PIN",
       "Wrong PIN.", "गलत पिन।", "Galat PIN.", PRIORITY_HIGH),
    _e("pin_locked", "Security", "PIN lockout",
       "Too many wrong attempts. Locked for now.", "बहुत गलत प्रयास। अब कुछ देर के लिए बंद है।",
       "Bahut galat attempts. Ab thodi der ke liye locked.", PRIORITY_HIGH),
    _e("locked", "Security", "Application locked",
       "Locked.", "ताला लगा दिया गया।", "Lock ho gaya.", PRIORITY_LOW),
)

BY_NAME: dict[str, VoiceEvent] = {event.name: event for event in EVENT_CATALOGUE}

# Words used in place of a customer's name when public mode is on.
ANONYMOUS = {LANGUAGE_ENGLISH: "the customer", LANGUAGE_HINDI: "ग्राहक", LANGUAGE_HINGLISH: "customer"}

# The two stored entry types. Call sites pass the raw database value; the screen labels ("Debit (Dr)") read
# badly aloud, so speech has its own words.
TYPE_WORDS = {
    "UDHAAR": {LANGUAGE_ENGLISH: "debit", LANGUAGE_HINDI: "उधार", LANGUAGE_HINGLISH: "udhaar"},
    "JAMA": {LANGUAGE_ENGLISH: "credit", LANGUAGE_HINDI: "जमा", LANGUAGE_HINGLISH: "jama"},
}

# "{count}" is spoken with its noun, because "1 entry" / "4 entries" needs language-specific endings.
COUNT_WORDS = {
    LANGUAGE_ENGLISH: ("entry", "entries"),
    LANGUAGE_HINDI: ("प्रविधि", "प्रविधियाँ"),
    LANGUAGE_HINGLISH: ("entry", "entries"),
}


def render(event: VoiceEvent, values: dict, language: str, public: bool = False) -> str:
    """Fill one template for one language, turning amounts into spoken words."""
    text = event.template(language)
    if not text:
        return ""
    data = dict(values)
    if public:
        data["customer"] = ANONYMOUS.get(language, "customer")
    spoken_type = TYPE_WORDS.get(str(data.get("type", "")).upper())
    if spoken_type:
        data["type"] = spoken_type.get(language, spoken_type[LANGUAGE_HINGLISH])
    count = data.get("count")
    if isinstance(count, int):
        singular, plural = COUNT_WORDS.get(language, COUNT_WORDS[LANGUAGE_HINGLISH])
        data["count"] = f"{count} {singular if count == 1 else plural}"
    for key in event.spoken:
        amount = data.get(key)
        if isinstance(amount, int):
            data[key] = amount_words(amount, language)
    for key, value in list(data.items()):
        if isinstance(value, int) and key.endswith("_paise"):
            data[key[:-7]] = amount_words(value, language)
    try:
        return text.format(**data).strip()
    except KeyError:
        return ""   # a template asking for a value the caller did not supply should stay silent, not crash a save
