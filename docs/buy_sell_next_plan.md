# BUY/SELL — Sonraki Plan (v1, 2026-08-28)

> **Amaç:** Sinyali şişirmek değil. Düzeltme-indiriminde oynanabilir long
> senaryosunun **READY’ye ulaşabilmesi**. Structure hâlâ yönü tek başına koyar.
> Sert kapılar (SHOCK, HIGH conflict, opportunity NONE, permission BLOCKED,
> 1D/1H yokluğu) durur.

## Evet: şu an yeni BOS olmadan işleme giremiyor

Permission OPEN yalnız `continuation == ALIGNED` iken açılır.
ALIGNED = **o ufkun anchor TF’inde** son *current* external olay tez yönünde **BOS**.

| Ufuk | Permission anchor | Timing TF | Bugün beklenen |
|---|---|---|---|
| LT | **1d** | 1h | Yeni **1d BOS** (haftalar/aylar geç) |
| ST | **1h** | 30m | Yeni **1h BOS** (düzeltme bitmiş) |

Üstüne LT timing, ST `PULLBACK` / `COUNTER_REACTION` iken **EARLY** durur
(`LOWER_HORIZON_COUNTER_MOVE_TO_RESOLVE`) — düzeltme bitsin diye bekler.
Alınacak yer tam o düzeltmedir.

**4h BOS hiç istenmez ve istenmeyecek.** 4h yalnız LT tezini TRANSITIONING
işaretleyebilir; yön koymaz, giriş teyidi değildir.

Tarihte trendi kuran BOS zaten `thesis INTACT` içinde. Giriş için **ikinci**
BOS beklenmez.

## Ne değişecek (üç yer, ufka göre farklı)

Giriş kuralı ortak: **tez var + indirim bölgesi + düşük TF teyit**.
Teyit TF’si ufka göre değişir. Yeni 1d/4h/1h BOS **yok**.

### 1. Permission — `context/permissions.py`

Catch-all `QUALIFIED_CONTINUATION_REACTION_OR_TRANSITION_CONTEXT` kalkmaz;
**düzeltme dalı** eklenir.

Tez INTACT + continuation ALIGNED değil (WEAK / CONFLICTING = pullback CHOCH)
+ conflict HIGH değil + karşı-BOS yok:

- **LT:** `scope=CONTINUATION_ONLY` (tez 1d; bu bir reversal değil),
  `gate=CONDITIONAL`, reason `PULLBACK_DISCOUNT_CONTEXT`.
  OPEN olmaz. Timing 1h reaksiyona devredilir.
- **ST:** aynı kural, `scope=REACTION_ONLY`, timing 30m.

HIGH / `CONFLICTING_BREAK` (karşı BOS) veto **aynı**.

### 2. LT timing — `decision/timing.py`

Bu satır **yalnız LT continuation BOS-sonrası** için kalsın:

`relation in {EARLY_TRANSITION, STRUCTURAL_CONFLICT} → EARLY`

`PULLBACK` / `COUNTER_REACTION` buradan **çıkar**. Düzeltme LT’yi kilitlemez;
LT setup’ı **1h reaksiyon** olur (zaten `_timeframe_policy` LT timing = 1h).

ST timing değişmez: 30m.

### 3. Setup — `decision/timing.py`

Birincil bölgede (T4 confirmation kümesi) setup **ABSENT değil FORMING**.
CONFIRMED hâlâ gerçek reaksiyon veya pattern break — BOS değil.

- LT CONFIRMED = 1h reaksiyon teyidi
- ST CONFIRMED = 30m reaksiyon teyidi

## Yapılmayacaklar

- 1d / 4h / 1h’de taze BOS beklemek
- 4h’i giriş teyidi yapmak
- T3 native volume, payload Faz 2, master skor, TF oylaması
- 1d pivot matematiğini gevşetmek
- Tek hisseden P/L eşiği

## Sıra

`1 permission + 2 LT-timing + 3 setup` tek commit (permission parmak izi →
**bir rebuild**). Sonra ASELS profili: ST veya LT QUALIFIED > 0, READY > 0,
WAIT hâlâ çoğunluk. Hâlâ 0 ise T5 ARMED. Sonra backtest dumanı.
