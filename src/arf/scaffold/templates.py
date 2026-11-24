"""Report and private-notes skeletons. `<!-- arf:slot name -->` marks text the writer fills."""

REPORT = {
    "he": """\
{draft}# דוח מוכנות לסוכני AI (AgentReady) — {business}

**הוכן עבור:** {business}{contact_line}
**הוכן על ידי:** Ben Blum — automatic evaluation
**תאריך הבדיקה:** <!-- arf:slot check-date -->
{meeting_line}
---

## תקציר מנהלים

**השורה התחתונה:** <!-- arf:slot bottom-line -->

**ציון חיצוני:** <!-- arf:slot external-score -->

**החדשות הטובות:** <!-- arf:slot good-news -->

**ההצעה:** <!-- arf:slot proposal -->

---

## 1. למה זה חשוב ל{business}

<!-- arf:slot why-it-matters -->

## 2. מה מצאנו

| # | ממצא | המשמעות העסקית |
|---|------|----------------|
<!-- arf:slot findings-rows -->

## 3. מה עובד היום

<!-- arf:slot what-works -->

## 4. מה אנחנו מציעים

<!-- arf:slot offer -->

## 5. הצעד הבא

<!-- arf:slot next-step -->

---

## נספח — מקורות ומועדי בדיקה (שעון ישראל)

| בדיקה | מקור | שעה | תוצאה |
|-------|------|-----|-------|
<!-- arf:slot sources-rows -->

<!-- arf:slot scope-note -->
""",
    "en": """\
{draft}# AI Agent Readiness Report (AgentReady) — {business}

**Prepared for:** {business}{contact_line}
**Prepared by:** Ben Blum — automatic evaluation
**Check date:** <!-- arf:slot check-date -->
{meeting_line}
---

## Executive summary

**Bottom line:** <!-- arf:slot bottom-line -->

**External score:** <!-- arf:slot external-score -->

**The good news:** <!-- arf:slot good-news -->

**The proposal:** <!-- arf:slot proposal -->

---

## 1. Why this matters for {business}

<!-- arf:slot why-it-matters -->

## 2. What we found

| # | Finding | Business impact |
|---|---------|-----------------|
<!-- arf:slot findings-rows -->

## 3. What works today

<!-- arf:slot what-works -->

## 4. What we propose

<!-- arf:slot offer -->

## 5. Next step

<!-- arf:slot next-step -->

---

## Appendix: sources and check times (Israel time)

| Check | Source | Time | Result |
|-------|--------|------|--------|
<!-- arf:slot sources-rows -->

<!-- arf:slot scope-note -->
""",
}

NOTES = {
    "he": """\
# הערות פרטיות לבן — שיחה עם {contact_name} ({business})

{meeting_line}*לא לשליחה. כל הבדיקות: <!-- arf:slot check-date --> (שעון ישראל).*

---

## נקודות לשיחה

<!-- arf:slot talking-points -->

---

## 3 שאלות ל{contact_name}

<!-- arf:slot questions -->

---

## הסתייגויות: מה לא נבדק או לא הוכח

<!-- arf:slot caveats -->
""",
    "en": """\
# Private notes for Ben — call with {contact_name} ({business})

{meeting_line}*Not for sending. All checks: <!-- arf:slot check-date --> (Israel time).*

---

## Talking points

<!-- arf:slot talking-points -->

---

## 3 questions for {contact_name}

<!-- arf:slot questions -->

---

## Caveats: what was not checked or proven

<!-- arf:slot caveats -->
""",
}

WEEKDAYS = {
    "he": ("יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת", "יום ראשון"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}

LABELS = {
    "he": {"meeting": "לקראת שיחה", "time": "שעון ישראל", "contact": "איש הקשר"},
    "en": {"meeting": "For the call", "time": "Israel time", "contact": "the contact"},
}
