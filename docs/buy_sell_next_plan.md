# BUY/SELL — Sonraki Plan (v1, 2026-08-28)

> **Amaç:** Sinyali şişirmek değil. Düzeltme-indiriminde oynanabilir bir long
> senaryosunun **READY’ye ulaşabilmesi**. Structure hâlâ yönü tek başına koyar.
> Sert kapılar (SHOCK, HIGH conflict, opportunity NONE, permission BLOCKED,
> 1D/1H yokluğu) durur.

## Ne kanıtlandı

| Kaynak | Sonuç |
|---|---|
| ASELS gerçek cache (1263 bar) | BUY=0. LT PRESENT ama QUALIFIED yok. ST QUALIFIED=0. Arbiter hep LT. |
| TREND sentetik (+%59 / 90 gün) | WAIT=180/180. ST 100 kez long senaryoyu **görüyor**, QUALIFIED=0. |
| Ortak mekanizma | Kapılar aynı barda AND. Düzeltme anında hepsi birden kapanıyor. |

T1/T2/T4/T6 uygulandı. T1 ölü: ST hiç QUALIFIED olmadığı için fallback ateşlenmiyor.

## Kök neden (kod)

Permission (`context/permissions.py`) yalnız şu bağlamlarda açılır:

- continuation **ALIGNED** = son current external olay tez yönünde **BOS**
- aktif reaksiyon / teyitli reversal

Düzeltmede son olay genelde karşı-CHOCH → continuation ALIGNED değil → varsayılan:

`QUALIFIED_CONTINUATION_REACTION_OR_TRANSITION_CONTEXT` → WAIT.

Piyasa matematiği ters: BOS, indirimin **bittiği** yerdir. Alınacak yer bölgeye
geliş / reaksiyon. Motor BOS’u bekleyerek indirimi kaçırıyor.

İkinci kilit: `SETUP_TRIGGER` reaksiyon CONFIRMED veya pattern BREAK_CONFIRMED
ister. Düzeltmede ikisi de yok → timing EARLY/DEVELOPING → QUALIFIED yok.

## Yapılacaklar (sırayla)

### Adım 0 — ASELS profili (sen, cache HIT)

Değişiklik yok. Mevcut kod + kalibrasyon. Rapordan bakılacak tek şey:

- LT presence PRESENT mi? (ASELS’te evetti; TREND’de 1D oluşmadı — sentetik)
- ST waiting dağılımı: `QUALIFIED_CONTINUATION…` vs `SETUP_TRIGGER`
- ST QUALIFIED hâlâ 0 mı?

Bu, Adım 1’in hedefini kilitler. Kod yazılmaz.

### Adım 1 — P0 Permission: düzeltme = geçerli kapsam (cache-güvenli)

`permissions.py` catch-all WAIT kalkmaz; **yeni bir dal** eklenir:

LT tez INTACT + ST PULLBACK/COUNTER_REACTION + conflict HIGH değil
→ `scope=REACTION_ONLY`, `gate=CONDITIONAL` veya `WAITING`
(`PULLBACK_DISCOUNT_CONTEXT`).

- HIGH / karşı-BOS veto **aynı**
- OPEN olmaz (hâlâ reaksiyon/trigger gerekir)
- `FUTURE_ACTION_LAYER_TIMING` zaten timing katmanına devredilmiş; bu dal
  boş WAIT üretmez

Beklenen: `QUALIFIED_CONTINUATION…` 95 → belirgin düşüş; ST DEVELOPING kalır
ama permission artık zinciri tek başına kilitlemez.

### Adım 2 — P0 Setup: birincil bölgede FORMING sayılır (cache-güvenli)

`timing.py` / reaksiyon: fiyat teyitli birincil bölgede (T4’ün confirmation
kümesi) iken setup ABSENT değil **FORMING**. CONFIRMED hâlâ gerçek reaksiyon
veya pattern break.

Beklenen: `SETUP_TRIGGER` waiting azalır; timing DEVELOPING → CONFIRMED olunca
READY. QUALIFIED ilk kez > 0 → T1 canlı.

### Adım 3 — Ölçüm

Aynı ASELS profili. Kabul:

- ST QUALIFIED > 0
- Arbiter ST seçimi > 0 **veya** LT QUALIFIED > 0
- READY > 0 (BUY bu profilde hâlâ yok: `execution_event=None`)
- WAIT hâlâ çoğunluk
- HIGH conflict / SHOCK / opportunity NONE sert

Hazır değilse Adım 4. Hazırsa Adım 5.

### Adım 4 — P1 T5 ARMED (yalnız Adım 3 başarısızsa)

Aynı bar AND kalkar: ARMED = conflict LOW + room OK + bölgede + trigger FORMING.
READY = ARMED + trigger CONFIRMED (sonraki kapalı bar). Kapı seti değişmez,
eşzamanlılık kalkar.

### Adım 5 — BUY dumanı

`buy_sell_backtest.py --canonical-readiness-proxy` (ASELS). İlk kez pozisyon
açılıyorsa hangi barda / hangi kapı düştü raporda görünür.

## Yapılmayacaklar (bu tur)

- T3 native `BREAKOUT_ABSORPTION` (engines/ → rebuild)
- Payload Faz 2 (source_refs sıkıştırma)
- 1D pivot matematiğini “trend görünmüyor” diye gevşetmek (ASELS’te LT vardı)
- Master skor, TF oylaması, zayıf hacmi gizleme
- Tek hisseden P/L ile eşik uydurma

## Uygulama sırası

`0 ölçüm → 1 permission → 2 setup → 3 ölçüm → (gerekirse 4 ARMED) → 5 backtest`

Adım 1+2 tek commit, `context/permissions.py` parmak izinde olduğu için
**bir rebuild**. `timing.py` küme dışında — yalnız 1 de rebuild ister.
İkisini birleştirmek: tek rebuild.
