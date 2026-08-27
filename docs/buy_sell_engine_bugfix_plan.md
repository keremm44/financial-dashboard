# BUY/SELL Motoru — Teşhis, Hata Listesi ve Onarım Yol Haritası

> **Tarih:** 2026-08-27
> **Girdi:** `entry_reason_profile.py` çıktısı — ASELS, 1263 snapshot,
> `HIT_EXACT_CACHE_ONLY`, cache 491.65 MB, timeline load 80.5 s, reason profile 199.2 s
> **Kapsam:** Decision katmanı (`src/financial_dashboard/decision/`), DecisionInput snapshot sözleşmesi, frozen timeline cache ve profil araçları.
> **Referans mimari:** `docs/buy_sell_decision_architecture_master.md`

---

## BÖLÜM 1 — Profil Çıktısının Teşhisi (ne oluyor?)

### 1.1 Zincir asla `QUALIFIED`'a ulaşamıyor (en kritik bulgu)

```
ENTRY ACTION   WAIT 1140 | NO_TRADE 123   → READY/BUY hiç yok
LT stage       DEVELOPING 1035 | BLOCKED 123 | UNAVAILABLE 105
LT eligibility WAITING 1140 | BLOCKED 123  → ELIGIBLE 0
```

1263 snapshot'ın **tekinde bile** `ELIGIBLE` / `QUALIFIED` / `READY` üretilmemiş.
Bu bir "piyasa koşulu" sorunu değil; **yapısal olarak ulaşılamaz durumda olan kapılar** var.
Üç kalıcı kapı tüm snapshot'ların neredeyse tamamını kilitliyor:

| Kapı (WAITING reason) | Sayı | Kalıcılık | Kök neden |
|---|---|---|---|
| `MATERIAL_CONFLICT_TO_RESOLVE` | 1035/1035 | **%100** | KN-1 (aşağıda) |
| `OPPORTUNITY_EVIDENCE_OR_CALIBRATION` | 1035/1035 | **%100** | KN-2 |
| `CONTEXT_CONFLICT_TO_RECONCILE` | 1035/1035 | **%100** | KN-1 türevi |

Üçünün de **%100 oranında** görünmesi, bunların piyasa kaynaklı değil **kod/kalibrasyon kaynaklı** olduğunu kanıtlıyor: gerçek piyasa verisinde hiçbir koşul 1263 bar boyunca değişmeden kalamaz.

### 1.2 Kök neden KN-1 — REACTION conflict ailesi "yapışkan" ve sınırsız tarama

```
MATERIAL CONFLICT FAMILY:  LT:REACTION  1263   (%100!)
CONFLICT FAMILY REASONS:
  LT:REACTION:REACTION_FAILED_WHILE_OTHER_REACTION_CONFIRMED  906
  LT:REACTION:REACTION_FAILED                                   357
```

**Kod yeri:** `src/financial_dashboard/decision/reaction.py` → `assess_reaction()`

Sorun mekanizması:

1. Fonksiyon, `order_blocks.observations` ve `fvg_engulfing.fvg/.engulfing` içindeki
   **tüm tarihsel gözlemleri** tarar (LT için `1d/4h/2h/1h` — `engine.py:_LT_REACTION_TIMEFRAMES`).
2. Bu gözlemler **terminal (ölümüş) kayıtları da içerir**: `CONSUMED`, `EXPIRED_CANDIDATE`,
   `full_fill`, `invalid` … Herhangi **bir** tanesi bile `failed=True` yapar.
3. ASELS gibi yıllarca verisi olan bir sembolde 4 timeframe'in herhangi birinde
   **her an en az bir ölmüş OB/FVG bulunur** → `failure_present` her snapshot'ta `True`.
4. `conflict.py:_reaction_evidence()` bunu doğrudan `MATERIAL`'a çevirir.
   Serbest bırakma (release) koşulu, yakınlık filtresi, zamansal sınırlama **yok**.
5. Sonuç: `ConflictState.MATERIAL` → `MATERIAL_CONFLICT_TO_RESOLVE` → sonsuz WAIT.

`REACTION_FAILED_WHILE_OTHER_REACTION_CONFIRMED`'un 906 kez görünmesi bunu tek başına kanıtlıyor:
aynı anda hem ölü bir bölge **hem de** onaylanmış bir reaksiyon var — yani tarama, birbiriyle
ilgisiz, farklı lineage'lara ait bölgeleri tek "aile oyu"nda birleştiriyor. Mimari doküman
("multiple refs inside one family never become multiple votes") aile içi çoklu referansın
oy yığmamasını söylüyor; ama burada aile oyu **ilgisiz bölgelerin birleşimiyle** üretiliyor.

**KN-1'in türevleri:**
- `CONFLICT STATE: LT:HIGH 123` → `assess_conflict()` kuralı "≥2 MATERIAL aile = HIGH".
  REACTION zaten daima MATERIAL olduğu için, PARTICIPATION (103) veya ENVIRONMENT (31)
  material olduğunda anında HIGH → `INDEPENDENT_FAMILY_CONFLICT_HIGH` → 123 × `NO_TRADE`.
  Yani 123 NO_TRADE'nin tamamı KN-1'in ikincil etkisi.
- Timing katmanı da `assess_reaction`'ı (1h) kullandığı için (`timing.py:assess_setup_trigger`)
  `SETUP_TRIGGER` waiting 674 büyük ölçüde KN-1'den besleniyor.
- `ST:PARTICIPATION:MATERIAL 1030` (aşağıda KN-4) ile birleşince ST tarafı da kilitli.

### 1.3 Kök neden KN-2 — Opportunity katmanı hiç kalibre edilmemiş

```
LT OPPORTUNITY: UNKNOWN 1263 (%100)      (ST de UNKNOWN 1263)
LT WAITING: OPPORTUNITY_EVIDENCE_OR_CALIBRATION 1035
```

**Kod yeri:** `src/financial_dashboard/decision/engine.py:42`
(`opportunity_calibration: OpportunityCalibration | None = None`) ve
`opportunity.py` (`calibration is None` → `OPPORTUNITY_CALIBRATION_REQUIRED` → `UNKNOWN`).

Mimari bilinçli olarak "sihirli ATR eşiği yok" diyip kalibrasyonu zorunlu kılmış — bu doğru bir
tasarım; **ama kalibrasyon üreten/bağlayan hiçbir hat yok.** `DecisionEngineConfig()` default'u
`None` ve replay/profile/live yollarının hiçbiri doldurmuyor. Yani:

- `room_atr` ve hedef kimliği snapshot'larda **mevcut** (LT presence PRESENT 1158,
  `_observed_opportunity()` True dönmüş olmalı ki presence PRESENT olsun),
- ama sınıflandırma kalıcı `UNKNOWN` → eligibility kalıcı WAIT.

Bu, "konum/lokasyon bulamama" hissinin ikinci yarısı: hedef mesafesi hesaplanıyor ama
"yeterli mi / sıkışık mı" kararı hiç verilemiyor.

### 1.4 Kök neden KN-4 — ST PARTICIPATION `heavy_conflict` tümör gibi

```
ST:PARTICIPATION:PARTICIPATION_HEAVY_CONFLICT 987/1263 (%78)
```

ST participation tek timeframe'e (1h) bakıyor (`engine.py:_timeframe_policy` → `"1h"`).
`heavy_conflict` native volume katmanından geliyor; `participation.py` onu görünce
dokunmadan `OPPOSING + MATERIAL` yapıyor. 987/1263 oranında material olması iki ihtimal:
(a) 1h'de gerçekten uzun süre ağır çelişki vardı (gerçek), ya da
(b) native tarafın release yolları (recovery/supersession/fake-reclaim — README'de sözü var)
projeksiyona/davranış satırına doğru taşınmıyor ve flag yapışkan kalıyor (bug).
**Ölçülmeden karar verilemez** — Faz 0'daki denetim bunu ayırt edecek.

### 1.5 Isınma kaybı — ilk 105 snapshot

```
LT presence UNKNOWN 105 → ARBITER WAITING_FOR_LONG_TERM_RESOLUTION 105
ENTRY_WAITING: LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE 105
```

İlk ~105 kararda 1d yapısı henüz oluşmamış; arbiter bilinçli olarak ST'ye düşmüyor
(`LONG_TERM_PRESENCE_UNRESOLVED_NO_SHORT_TERM_FALLBACK`). Bu tasarım tercihi doğru ama
(a) 1263 snapshot'ın %8'i bilgi üretmiyor, (b) profil raporunda bunun "warmup" olarak
etiketlenmesi gerekiyor ki gerçek oranlar yanılmasın.

### 1.6 Performans — üç ayrı sorun

```
FROZEN_CACHE_FILE_MB 491.65 | LOAD 80.5 s | REASON_PROFILE 199.2 s
```

- **P1 (profile script):** Döngüde her snapshot için `assess_entry_scenario` ×2
  (her biri `assess_horizon_decision` çağırır) + `assess_horizon_decision` ×2 daha +
  `assess_entry_decision` → `entry_arbitration` → **yine** horizon decision ×2 + QUALIFIED
  ise 1 tane daha. Ayrıca `assess_horizon_decision` içinde `assess_reaction` **iki kez**
  çalışıyor (geniş TF seti + timing TF)._snapshot başına ~5–7 tam zincir, her biri tüm
  OB/FVG gözlemlerini yeniden tarıyor → 158 ms/snapshot × 1263 ≈ 199 s.
- **P2 (cache boyutu):** Her snapshot tüm tarihsel projeksiyonları (bütün terminal OB/FVG
  kayıtları + FactRef'ler) taşıdığı için pickle ~492 MB. Boyut O(snapshots × full history).
- **P3 (load):** 492 MB pickle'ı tek parça deserialize etmek 80 s. Ayrıca
  `HIT_EXACT_CACHE_ONLY`: identity'de herhangi bir değişiklik tüm cache'i geçersiz kılıyor.

### 1.7 Küçük ama not edilmesi gerekenler

- `ARBITER SELECTION: LONG_TERM 1158 / UNRESOLVED 105` — arbiter **her zaman** LT'yi seçiyor,
  ST 817 kez ezilmiş (`SHORT_TERM_SCENARIO_SUPPRESSED_BY_LONG_TERM`). Mimari "BLOCKED LT,
  ST tarafından sessizce bypass edilemez" diyor; fakat **DEVELOPING** LT'nin kalıcı olarak
  nitelikli (qualified) bir ST setup'ı da bloklaması ürün kararı olarak gözden geçirilmeli
  (Faz 5, opsiyonel).
- Profil script'i `execution_event=None` ile çağrı yaptığı için BUY **tasarım gereği**
  çıkamaz; ama READY de hiç çıkmıyor → kapıların gerçekten kapalı olduğu doğrulanıyor.
- `TARGET_PATH_DEFENSE_REQUIRES_REASSESSMENT 106` / `ACTIVE_TARGET_PATH_NODE_DEFENDED 106/86`
  — savunma (defended) durumundan çıkış koşulları da incelenmeli; 106 snapshot'ta yeniden
  değerlendirme bekleniyor.

---

## BÖLÜM 2 — Hata / Görev Listesi (öncelik sıralı)

| ID | Öncelik | Başlık | Dosya | Tür |
|---|---|---|---|---|
| KN-1 | **P0** | Reaction failure bayrağı global + sticky; kapsam/release semantiği yok | `decision/reaction.py`, `decision/conflict.py` | Doğruluk |
| KN-2 | **P0** | Opportunity kalibrasyonu hiç bağlanmamış (`None` default) | `decision/engine.py`, `decision/opportunity.py` | Eksik özellik |
| KN-4 | **P1** | ST `heavy_conflict` release yollarının projeksiyona akışı doğrulanmamış | `context/participation_behavior_projection.py`, `domains/volume` | Denetim + olası bug |
| P1 | **P1** | Profile script'i zinciri snapshot başına 5–7 kez hesaplıyor | `scripts/entry_reason_profile.py` | Performans |
| KN-3 | **P1** | HIGH conflict kuralı KN-1 düzelince yeniden dengelenecek | `decision/conflict.py` | Doğruluk (KN-1 sonrası) |
| P2 | **P2** | 492 MB frozen cache: terminal gözlem taşımı + sıkıştırma + şeritli saklama | `decision/history_single_pass.py`, `persistent_state.py` | Performans/ölçek |
| P3 | **P2** | 80 s pickle load; parçalı/tembel yükleme | `decision/timeline_cache.py` | Performans |
| W1 | **P3** | Warmup 105 snapshot'ın raporda etiketlenmesi / kırpılması | `scripts/entry_reason_profile.py` | Rapor kalitesi |
| W2 | **P3** | Target-path DEFENDED release koşullarının gözden geçirilmesi | `decision/target_path.py` | İnceleme |
| W3 | **P3** | "DEVELOPING LT, QUALIFIED ST'yi kalıcı eziyor" ürün kararı | `decision/arbiter.py` | Tasarım incelemesi |

---

## BÖLÜM 3 — Fazlı Uygulama Planı

### FAZ 0 — Diagnostik sertleştirme (yarım gün, **önce bu**)

Amaç: KN-1/KN-4 hipotezlerini ölçerek doğrulamak ve sonraki fazların kazançlarını
sayısal olarak karşılaştırılabilir kılmak. `scripts/entry_reason_profile.py`'ye ekler:

1. **REACTION kaynak dökümü:** her snapshot için, failure'a yol açan gözlemleri
   `kaynak_tf:kaynak_tip:yaş_bar` olarak say (ör. `OB_FAILED:30m:412`).
   Hipotez testi: failure kaynağı > %90 oranında *terminal + eski + fiyatın uzağındaki*
   bölgelerse KN-1 kanıtlanır.
2. **Conflict clear-rate:** sembolün timeline'ında `MATERIAL→NONE/LOW` geçiş sayısı
   (şu an tanım gereği 0 olmalı; düzeltmeden sonra > 0 olmalı).
3. **`heavy_conflict` süre analizi:** ST 1h satırında `heavy_conflict=True` olan ardışık
   snapshot sayısının dağılımı (maks/medyan). Uzun kuyruk → sticky flag şüphesi.
4. **İlk geçilebilir snapshot:** hangi kapı sırasıyla düşüyor, zaman içinde ilk kez
   "yalnızca tek kapı" kalan snapshot index'i.
5. **Warmup etiketi:** `LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE`'u "WARMUP" olarak ayrı raporla.
6. **Opportunity odağı:** `room_atr` dağılımını (min/p25/medyan/p75/max) yazdır —
   Faz 2 kalibrasyon sınırlarının ilk taslağı bu.
7. **READY sayacı:** `execution_event=None` iken action `READY` da olabilir; raporda
   `READY`'yi ayrı göster (BUY zaten bu profilde imkânsız — script comment'ine yaz).

**Çıkış kriteri:** Yukarıdaki 6 metriğin ASELS timeline'ında çıktısı elinizde.

### FAZ 1 — KN-1: Reaction kapsam (relevance) ve release semantiği (2–4 gün)

İki katmanlı düzeltme öneriyoruz — **semantik zayıflatma değil, kapsam daraltma**:

**1a. İlgili bölge kümesi (relevant zone set):** `assess_reaction` yalnızca şu gözlemleri
dikkate alsın:
- `active=True` **veya** terminal olduktan sonra `age_bars ≤ R` olan (R kalibrasyon
  parametresi, başlangıç önerisi: o TF'nin ~30–50 barı; sabit sayı değil config'e koy),
- **ve** `distance_atr ≤ D` (bölge ile fiyat arası; başlangıç önerisi 3–5 ATR),
- **ve** horizon TF politikasına zaten uyan (mevcut filtre korunur).

**1b. Release kuralları:** failure şu durumlarda sönmeli:
- **Supersession:** aynı lineage (veya aynı bölge civarı) için **daha yeni** bir
  onaylanmış/aktif reaksiyon geldiyse eski failure aile oyuna katılmaz.
- **Mesafe release:** fiyat bölgeden `D` ATR'den uzaklaştıysa failure "eski haber"dir.
- **Yaş release:** terminal kaydın yaşı `R` barı aşarsa artık conflict üretmez
  (analitik geçmiş olarak kalmaya devam eder — silinmiyor, sadece conflict oyu vermiyor).

**1c. Aile oyu kapsamı:** `conflict.py:_reaction_evidence` içinde
`MIXED_CONFIRMED_AND_FAILED` yalnızca **aynı ilgili küme içinde** ikisi de varsa
MATERIAL olsun; ilgisiz lineage'lar birbirini "çelişki" saymasın.

**Uygulama notları:**
- Filtre mantığını `reaction.py` içinde ayrı bir `select_relevant_zones(...)`
  fonksiyonuna alın — hem test edilir hem Faz 1d'de `timing_reaction` da aynı küreyi
  paylaşır (ikinci tarama = P1 performans kazancı da beraberinde gelir).
- `R` ve `D` değerlerini `DecisionEngineConfig`'e `reaction_relevance_max_age_bars` /
  `reaction_relevance_max_distance_atr` olarak ekleyin; mimari kural gereği
  **kalibrasyon parametresi** olarak etiketleyin (§"Forbidden before calibration").
- Terminal olmayan tek bir ölü bölge bile `SETUP_TRIGGER:FAILED`'i tetiklemeye devam
  etmeli — timing gerçek bir başarısız reaksiyonu hâlâ yakalamalı. Kısacası kapsam
  daralıyor, semantik korunuyor.

**Testler (yeni):**
- `tests/decision/test_decision_reaction_relevance.py`
  - çok eski terminal OB → MATERIAL üretmemeli,
  - aktif + yakın failed FVG → MATERIAL üretmeli,
  - aynı lineage'a yeni onay → eski failure release,
  - fiyat 5 ATR uzaklaşınca → failure release,
  - `MIXED` yalnızca ilgili küme içinde,
- `tests/decision/test_decision_conflict_clearability.py`
  - sentetik fixture: 1000 bar yükselen trend → `MATERIAL` oranı < %20 olmalı
    (bugün %100; bu bir **invariant test** olarak kalıcı eklenmeli).

**Çıkış kriteri (ASELS profilinde):** `LT:REACTION:MATERIAL` %100 → **< %35**;
`LT:HIGH` 123 → < 20; `CONFLICT clear-rate > 0`.

### FAZ 2 — KN-2: Opportunity kalibrasyon hattı (2–3 gün)

1. **Kalibrasyon üretici:** `scripts/build_opportunity_calibration.py`
   - frozen timeline üzerinde, yapısal yön LONG olduğu anlardaki `room_atr`'yi ve
     ileriye dönük gerçekleşme (MFE, ATR cinsinden) dağılımını hesaplar,
   - sınırlar: `none_max` (medyan MFE'nin alt çeyreği civarı), `compressed_max`,
     `moderate_max` — per-sembol (+ tercihen rejim kırılımı) çıktı:
     `storage/calibration/opportunity/{symbol}.json`
   - JSON şeması sürümlü olsun (`{"version":1, "symbol":"ASELS", "windows": {...}}`).
2. **Bağlama:** `DecisionEngineConfig`'e kalibrasyonu dosyadan okuyan loader
   (`load_opportunity_calibration(path)`) + replay/profile/backtest script'lerine
   `--opportunity-calibration` argümanı. Default davranış (kalibrasyonsuz) `UNKNOWN`
   kalmaya devam etsin — fail-closed felsefesi korunur.
3. **Guard:** kalibrasyon dosyasının üretildiği veri penceresi ile kullanıldığı pencere
   çakışıyorsa profilde `IN_SAMPLE_CALIBRATION_WARNING` bas (ileriye sızmayı görünür kılmak için).

**Testler:** `tests/decision/test_opportunity_calibration_loader.py`
(şema, monotonic sınır zorunluluğu zaten `__post_init__`'ta var), end-to-end:
kalibrasyonlu config ile en az 1 `ELIGIBLE` snapshot üreten sentetik fixture.

**Çıkış kriteri:** `LT OPPORTUNITY: UNKNOWN` %100 → UNKNOWN yalnızca hedef gerçekten
yoksa; `OPPORTUNITY_EVIDENCE_OR_CALIBRATION` waiting sayısı dramatik düşer;
profilde `AMPLE/MODERATE/COMPRESSED` dağılımı görünür.

### FAZ 3 — KN-4: Participation release denetimi (1–2 gün)

1. Faz 0'daki `heavy_conflict` süre analizinin sonucuna göre:
   - **Sticky değilse (gerçek):** dokunma; yalnızza raporlama/sınıflandırma sağlıklı demektir.
   - **Sticky ise:** native volume katmanındaki recovery/supersession/fake-reclaim
     yollarının `ParticipationBehaviorProjection` satırına akışını incele
     (`context/participation_behavior_projection.py` + `domains/volume`),
     eksikse native tarafa release taşıma task'ı aç (decision katmanında maskelenecek
     şey değil — mimari kural: supporting domain kendi release'ini üretir).
2. ST participation'ın tek TF (1h) yerine `1h + 30m` teyit kümesine bakması tartışılsın
   (tek satırın bir native flag'i tüm ST'yi %78 oranında MATERIAL yapabiliyor — kırılganlık).

**Çıkış kriteri:** `ST:PARTICIPATION:MATERIAL` veya belgelenmiş "gerçek" gerekçesi.

### FAZ 4 — Performans (2–3 gün; KN düzeltmelerinden sonra, ölçüm temiz olsun)

**P1 — Profile/pipeline yinelemesizleştirme (kolay, büyük kazanç):**
- Döngüde snapshot başına: `lt_decision`/`st_decision`'ı **bir kez** hesapla,
  `assess_entry_scenario`'ya assessment'ı parametre olarak geçir (yeni opsiyonel argüman;
  mevcut imza bozulmaz), arbiter/entry zinciri de aynı assessment'ları kullansın.
- `assess_horizon_decision` içinde `reaction` ile `timing_reaction` aynı filtrelenmiş
  küreyi paylaşsın (Faz 1'in `select_relevant_zones` çıktısı).
- Hedef: **REASON_PROFILE_SECONDS < 20** (10× kazanç).

**P2 — Cache boyutu:**
- Snapshot'a konan projeksiyonlarda terminal gözlemleri kırp (analitik geçmiş ayrı
  saklansın), `FactRef`'lerde tekrar eden alanları (symbol/as_of…) referansla/intern et,
  pickle sonrası **zstd/lz4** sıkıştırma uygula.
- Hedef: **< 150 MB** (3×+ küçülme).
- Not: faz 1 relevance filtresi zaten karar için terminal tarihin tamamına ihtiyaç
  bırakmayacağı için kırpmak güvenli hale geliyor.

**P3 — Load:**
- Timeline'ı snapshot şeritleri halinde sakla (ör. 128'lük chunk'lar) ve profil
  araçlarında tembel/paralel yükle; veya `PersistentObjectStore`'a codec katmanı ekle.
- Hedef: **load < 15 s**.
- Cache identity'sinde config alt kümesi (yalnız karar girdilerini etkileyen alanlar)
  parmak izine girsin; salt-raporlama alanları cache'i bust etmesin.

### FAZ 5 — Uçtan uca doğrulama ve tasarım incelemeleri (2 gün)

1. **Kabul profili (ASELS + 2–3 sembol daha):**
   - `ELIGIBLE ≥ %5`, `QUALIFIED ≥ %2`, `READY ≥ %1` snapshot (trendli pencerelerde),
   - `MATERIAL` ailelerin her biri < %35, `HIGH` < %5,
   - `ENTRY ACTION` dağılımında `WAIT/NO_TRADE` dışı kategori görünüyor.
2. **Backtest smoke:** `scripts/buy_sell_backtest.py` ile en az bir sembolde tam döngü
   (sinyal → pozisyon → exit) koşusu; pozisyon açılamıyorsa hangi kapıdan çıktığı
   profile'da görünüyor olmalı ("konum bulamama" artık ölçülebilir ve izah edilebilir).
3. **Invariant test paketi (kalıcı):**
   - `MATERIAL conflict clearable` (Faz 1 fixture'ı),
   - `QUALIFIED reachable` (sentetik trend),
   - `opportunity UNKNOWN yalnızca hedef yokken`,
   - `READY requires execution_event=False path`,
   - cache round-trip eşdeğerlik testi mevcutsa (history_single_pass equivalence) yeni
     projeksiyon kırpmasıyla yeniden yeşil.
4. **Tasarım incelemeleri (W2, W3):** target-path DEFENDED release; DEVELOPING LT vs
   QUALIFIED ST sahiplik politikası. Her biri ayrı kısa ADR (architecture decision
   record) olarak `docs/` altına yazılsın.

---

## BÖLÜM 4 — Bu Hafta Uygulanabilir Hızlı Kazanımlar

1. **Profile'a `--opportunity-calibration` geçici parametresi** + el ile sınır
   denemesi (ör. none=0.5 / compressed=1.5 / moderate=3 ATR) → `MATERIAL_CONFLICT`
   olmayan anlarda zincirin geri kalanının çalıştığını **görmek** (KN-2'nin etkisini
   izole etmek için; kalıcı sınırlar Faz 2'de veriden üretilir).
2. **Scratch dalda 5 satırlık reaction filtresi** (`active or age_bars <= 50`) ile
   profili yeniden koş → KN-1'in tek başına kaç WAIT'i açtığını ölç.
3. **Profile döngüsünde yinelemesizleştirme (P1)** — kn düzeltmelerinden bağımsız,
   her iterasyonu ~10× hızlandırır; deneme-yanılma döngünüzü kısaltır.
4. **`REACTION kaynak dökümü`** (Faz 0 madde 1) — tek yarım gün, en yüksek bilgi/kâr.

## BÖLÜM 5 — Riskler ve Prensipler

- **Semantiği directionsuyla zayıflatmayın:** Faz 1 "kapsam daraltma"dır; gerçek ve
  güncel bir reaksiyon başarısızlığı hâlâ MATERIAL olmalı. Kolaylaştırma değil,
  yerindelik (relevance) getiriyoruz.
- **Her yeni eşik kalibrasyon parametresi olarak etiketlensin** (`R`, `D`, opportunity
  sınırları) — mimari doküman §266–281 ve §988 bunu zorunlu kılıyor; default'lar
  "başlangıç önerisi" olarak kalsın, kalıcı değerler Faz 2 hattından gelsin.
- **Decision katmanında maskeleme yok:** participation/volume release'i native tarafa
  aittir; decision yalnızca consume eder.
- **Ölçmeden değiştirme yok:** her fazın önü Faz 0 metriğiyle, sonu aynı metrikle
  doğrulanır (before/after profil çıktıları `docs/` altında saklansın).
- **Cache formatı değişimlerinde eşdeğerlik testini koruyun:**
  `test_decision_history_single_pass_equivalence.py` gibi mevcut sözleşme testleri
  kırılırsa bilinçli migration notu yazın.

---

## BÖLÜM 6 — Detaylı Uygulama Şartnamesi (onaylanan v1 seti, 6 değişiklik)

> **DURUM: UYGULANDI** (2026-08-27, `arena/01a044cb-financial-dashboard`).
> 1–6'nın tamamı kodlandı; tam test paketi **1138 passed / 4 skipped**.
> Bilinçli fixture güncellemesi: `tests/test_decision_engine.py::_snapshot`
> gerçek sözleşmedeki `current_price` alanı ile tamamlandı.
> Sonraki adım kullanıcı koşusu: ASELS profili yeni sayaçlarla + kalibrasyon üretimi.

### 6.0 Terminoloji ve ortak kurallar

- **Terminal gözlem:** ölü/bitmiş bölge. OB: `state ∈ {CONSUMED, EXPIRED_CANDIDATE}`
  veya `interaction == FAILED`. FVG: `invalid` veya `full_fill`
  (`failed_reaction` **canlı** başarısızlıktır, terminal DEĞİLDİR).
  Engulfing: `invalid`.
- **Yaş (age_bars):** OB için native `age_bars` alanı; FVG/Engulfing için
  `(ref.confirmed_at − ref.origin_time) / TF_süresi` (zaman türevi, kendi TF biriminde).
  Hesaplanamıyorsa `None`.
- **Mesafe (dist_atr):** OB için native `distance_atr`; FVG için
  `min(|price−lower|, |price−upper|) / formation_atr`; Engulfing için filtre uygulanmaz
  (ATR alanı yok; confirmation-only katman).
- **Fail-closed kuralı:** *terminal* + (yaş bilinmiyor ∨ mesafe bilinmiyor) → kapsamdan
  çıkar (kronik conflict'e karşı kapalı). *Canlı* bölge + mesafe bilinmiyor → kapsamda
  kalır (canlı kanıt sessizce düşürülmez).

### 6.1 Değişiklik 1 — `decision/reaction.py`: kapsam + release semantiği

```python
@dataclass(frozen=True, slots=True)
class ReactionRelevancePolicy:
    max_age_bars: int | None = 50        # A: terminal bölge o TF'de bu yaşı aşarsa oy vermez
    max_distance_atr: float | None = 5.0 # D: fiyatın bu kadar ATR uzağındaki bölge oy vermez
    supersession: bool = True            # aynı TF'de çakışan confirmed bölge failure'ı söndürür
    # None sınırlar = sınır kapalı (legacy davranış için kullanılabilir)

def select_relevant_zones(
    order_blocks: OrderBlockBehaviorProjection | None,
    fvg_engulfing: FvgEngulfingLifecycleProjection | None,
    *,
    current_price: float,
    policy: ReactionRelevancePolicy,
) -> tuple[OrderBlockBehaviorProjection | None, FvgEngulfingLifecycleProjection | None]
```

- Kural: `relevant(z) = (¬terminal(z) ∨ age(z) ≤ A) ∧ (dist(z) ≤ D ∨ (dist unknown ∧ ¬terminal(z)))`
- `select_relevant_zones` yalnız **yaş/mesafe** ön filtresi uygular; projeksiyon
  `dataclasses.replace` ile küçültülür (kaynak silinmez).
- **Supersession** `assess_reaction` içinde: `relevance.supersession` doğruysa,
  failure olarak sınıflanan bölgeyle **aynı TF'de aralıkları çakışan** bir confirmed
  bölge varsa failure oya katılmaz (`*_FAILED_SUPERSEDED:<tf>:<id>` reason ile görünür kalır).
- `assess_reaction(...)` imzasına opsiyonel `relevance: ReactionRelevancePolicy | None = None`
  eklenir (yalnız supersession bayrağını tüketir). `relevance=None` → bugünkü davranış.

### 6.2 Değişiklik 2 — `decision/engine.py`: config + ortak reaksiyon küresi

- `DecisionEngineConfig.reaction_relevance: ReactionRelevancePolicy = ReactionRelevancePolicy()`
  (**varsayılan AÇIK** — KN-1 düzeltmesi aktif; legacy için `reaction_relevance=None` yazılır
  ya da profil script'inde `--legacy-reaction` kullanılır).
- `assess_horizon_decision` içinde `select_relevant_zones` **bir kez** çağrılır; filtreli
  projeksiyonlar hem `reaction` hem `timing_reaction` tarafından paylaşılır
  (aynı tarihin 2× taranması kalkar).

### 6.3 Değişiklik 3 — `scripts/build_opportunity_calibration.py` (yeni)

- Girdi: `cache_root symbol [--start --end --max-bars --pattern-profile]`
  `--forward-bars H (default 24)` `--quantiles q1,q2,q3 (default 0.25,0.5,0.75)`
  `--min-samples (default 50)` `--output (default {cache_root}/calibration/opportunity/{symbol}.json)`
- Yöntem: frozen timeline yüklenir (`load_frozen_decision_timeline`, profil ile aynı
  warmup kuralları). Her snapshot × horizon için:
  - `structural.direction ∈ {LONG, SHORT}` değilse örnek dışı;
  - `room_atr = decision.opportunity.room_atr`; `None` ise örnek dışı;
  - yön LONG ise `mfe_atr = (max(high[i+1..i+H]) − price) / reference_atr`,
    SHORT ise `(price − min(low[i+1..i+H])) / reference_atr`
    (`reference_atr = snapshot.targeting.reference_atr`, fiyat biriminde);
  - ileri pencere taşarsa örnek dışı (son H bar kalibrasyona girmez).
- Sınırlar: `mfe_atr` dağılımının kantilleri → `none_max=q1, compressed_max=q2,
  moderate_max=q3`. `OpportunityCalibration.__post_init__` strict-artanlık guard'ı zaten var;
  dejenere dağılım (eşit kuantiller) → açık hata.
- Çıktı JSON şeması (deterministik; wall-clock YOK):
  ```json
  {"version": 1, "kind": "opportunity", "symbol": "ASELS",
   "boundaries": {"none_max_atr": 0, "compressed_max_atr": 0, "moderate_max_atr": 0},
   "sample_size": 1234, "forward_bars": 24, "quantiles": [0.25, 0.5, 0.75],
   "reference_timeframe": "1h", "source_identity": "<cache identity>"}
  ```

### 6.4 Değişiklik 4 — `decision/calibration.py` (yeni)

```python
class CalibrationSchemaError(ValueError): ...

@dataclass(frozen=True, slots=True)
class OpportunityCalibrationRecord:
    calibration: OpportunityCalibration
    symbol: str
    sample_size: int
    version: int
    meta: Mapping[str, object]        # forward_bars, quantiles, source_identity, ...

def save_opportunity_calibration(path: Path, record: OpportunityCalibrationRecord) -> None
    # atomik yazım: tmp dosya + os.replace; pretty JSON

def load_opportunity_calibration(path: Path) -> OpportunityCalibrationRecord
    # version==1, kind=="opportunity", boundaries sayısal ve strict-artan
    # (OpportunityCalibration kendi doğrulamasını yapar); sembol alanı bilgi olarak taşınır
```

### 6.5 Değişiklik 5 — `scripts/entry_reason_profile.py`: ölçüm + yinelemesizleştirme

- Yeni CLI: `--opportunity-calibration PATH` (6.4 loader'ı ile yüklenir),
  `--legacy-reaction` (relevance policy'yi kapatır, A/B ölçümü için).
- Hesap paylaşımı (P1): snapshot başına `assess_horizon_decision` **1×/horizon**;
  senaryo → arbiter → entry zinciri hesapları parametre olarak enjekte edilir.
  Geriye dönük uyumlu opsiyonel API eklemeleri:
  - `assess_entry_scenario(snapshot, horizon, *, config=None, assessment=None)`
  - `assess_entry_arbitration(snapshot, *, config=None, scenarios=None)`
  - `assess_entry_decision(snapshot, *, config=None, execution_event=None, arbitration=None, assessments=None)`
- Faz 0 sayaçları (Ek B'deki tüm bölümler):
  1. `REACTION FAILURE SOURCES` — terminal-failed bölgeler `tf:tip:yaş-aralığı:mesafe-aralığı` bazında (legacy küre üzerinden; KN-1 kanıtı)
  2. `REACTION RELEVANT SET SIZE` — snapshot başına ilgili bölge sayısı dağılımı (min/med/p90/max)
  3. `CONFLICT TRANSITIONS` — horizon bazında `MATERIAL→{NONE,LOW}` geçiş sayısı (clear-rate)
  4. `HEAVY_CONFLICT RUN LENGTHS` — 1h satırında ardışık `heavy_conflict=True` uzunluk dağılımı (KN-4 ölçümü)
  5. `OPPORTUNITY ROOM ATR DIST` — min/p25/medyan/p75/max
  6. `FIRST SINGLE-GATE SNAPSHOT` — `waiting_for` uzunluğu 1'e düşen ilk snapshot (index + timestamp + kalan kapı)
  7. `WARMUP SNAPSHOTS` — LT presence UNKNOWN sayısı ayrı satırda
  8. `READY` eylem sayacı mevcut `ENTRY ACTION` bölümünde görünür (BUY bu profilde imkânsız: `execution_event=None` — script başlığına not düşülür)

### 6.6 Değişiklik 6 — testler (yeni 2 dosya)

`tests/test_decision_reaction_relevance.py`

| # | Senaryo | Beklenen |
|---|---|---|
| T1 | 900 bar yaşında terminal `CONSUMED` OB | default policy: `failure_present=False`; `relevance=None`: `True` |
| T2 | 900 bar yaşında ama `active` OB (hiç ölmemiş) | kapsamda kalır (yaş sınırı canlıya uygulanmaz) |
| T3 | yaş 5 ama mesafe 12 ATR terminal bölge | kapsam dışı (mesafe release) |
| T4 | `full_fill` yaşlı FVG dışarıda; genç `invalid` FVG içeride | yaş kuralı TF-bağımsız çalışır |
| T5 | failed bölge + aynı TF'de **çakışan** confirmed bölge | failure söndürülür (`SUPERSEDED` reason), confirmed kalır |
| T6 | genç canlı `failed_reaction` FVG | failure kalır (semantik korunur — zayıflatma değil) |
| T7 | mesafesi `None` canlı OB | kapsamda (fail-open yalnız canlıya) |
| T8 | FVG yaşı zaman türetmeli hesap | `(confirmed_at−origin_time)/TF süresi` doğru bar sayısına düşer |

`tests/test_decision_conflict_clearability.py`

| # | Senaryo | Beklenen |
|---|---|---|
| C1 | genç failure barlarında MATERIAL conflict → aynı bölgeler yaşlanınca | `MATERIAL → NONE` geçişi gözlenir (clearability) |
| C2 | 1000 terminal-failed bölge (yaş 1..1000) + güncel fiyat | default policy: failure yalnız `age ≤ A` kümesinden; relevant set küçük |
| C3 | aynı fixture, `relevance=None` | patholojik MATERIAL (bugünkü davranışın regresyon belgesi) |
| C4 | `select_relevant_zones`: None girdi → (None, None); kaynak projeksiyon değişmez | immutability |

### 6.7 Kabul kriterleri (kod düzeyi)

1. `pytest` paketi yeşil (reaksiyon davranışına bağlı mevcut testler bilinçli güncellenir).
2. Profil script'i mevcut çıktı bölümlerini birebir korur; yeni bölümler Ek B sırasıyla eklenir.
3. `--legacy-reaction` ve `--opportunity-calibration` flag'leri çalışır; kalibrasyonsuz
   varsayılan davranış fail-closed (`UNKNOWN`) kalır.
4. Kullanıcının ASELS koşusunda (sonraki ölçüm) Faz 1 çıkış kriteri:
   `LT:REACTION:MATERIAL < %35`, `CONFLICT clear-rate > 0`.
5. Kalibrasyon script'i deterministik: aynı cache + aynı argümanlar → bit-bit aynı JSON.

---

## Ek A — Önerilen yeni dosyalar

```
scripts/build_opportunity_calibration.py        (Faz 2)
src/financial_dashboard/decision/calibration.py (loader + şema, Faz 2)
tests/decision/test_decision_reaction_relevance.py      (Faz 1)
tests/decision/test_decision_conflict_clearability.py   (Faz 1, invariant)
tests/decision/test_opportunity_calibration_loader.py   (Faz 2)
docs/adr/0001-lt-developing-vs-st-qualified-ownership.md (Faz 5, W3)
docs/adr/0002-target-path-defended-release.md            (Faz 5, W2)
```

## Ek B — Profil çıktısına eklenmesi önerilen yeni bölümler

```
REACTION FAILURE SOURCES      (tf:tip:yaş aralığı bazlı sayaç)
REACTION RELEVANT SET SIZE    (snapshot başına ilgili bölge sayısı dağılımı)
CONFLICT CLEAR RATE           (MATERIAL->NONE/LOW geçiş sayısı)
HEAVY_CONFLICT RUN LENGTHS    (ST 1h, ardışık True uzunlukları)
OPPORTUNITY ROOM ATR DIST     (min/p25/med/p75/max)
FIRST SINGLE-GATE SNAPSHOT    (yalnız tek WAIT kapısı kalan ilk index)
WARMUP SNAPSHOTS              (LT presence UNKNOWN sayısı, ayrı etiket)
```
