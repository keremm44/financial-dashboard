# Independent Early-Rise Percent Audit — Code-Level Diagnostic Report

**Date:** 2026-08-30
**Symbol:** ASELS | **Moves:** 20 (largest rise → smallest rise)
**Rule:** price-only 4H, min=+7%, reversal=5%
**Auditor:** Independent code-level review of `financial-dashboard` decision engine

---

## 1. En Kritik 10 Problem

### P1 — Counter-LT Kapısı Sürekli Beklemede Kalıyor (Move #4, #5, #10, #17, #18)
**Semptom:** LT SHORT iken ST LONG setup oluştuğunda, sistem `COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM` ve `COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP` ile kalıcı WAIT üretiyor. Toplam 5/20 hareket bu nedenle kaçırılıyor.

**Kök Neden:** `engine.py::_apply_counter_lt_st_risk()` fonksiyonu.

```python
# engine.py, satır ~240
if timing.state is not TimingState.READY:
    waiting.append("COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP")
if opportunity.state not in {OpportunityState.MODERATE, OpportunityState.AMPLE}:
    waiting.append("COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM")
```

Bu iki koşul AND olarak çalışıyor. Opportunity calibration yoksa (veya hedef gözlemlenmiyorsa), `opportunity.state` her zaman `UNKNOWN` kalıyor → `COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM` asla temizlenmiyor → sistem sonsuz WAIT üretiyor.

**Neden Sorun Yaratıyor:** ASELS gibi hisselerde LT yapı sık sık SHORT/BEARISH iken ST reversal'lar gerçekleşiyor. Bu kapı çok sert olduğu için tüm counter-LT setup'lar bloke ediliyor.

**Düzeltme:**
- `OpportunityState.UNKNOWN` + armed timing durumunda soft override ekle
- Veya counter-LT için opportunity kalibrasyonunu opsiyonel yap (mevcut `hard_room_constraint=False` mekanizmasını kullan)
- Alternatif: timing=READY iken opportunity=UNKNOWN'ı soft reason olarak değerlendir, waiting'e ekleme

---

### P2 — Opportunity Calibration Yokluğunda Sürekli UNKNOWN (Move #4, #5, #9, #10, #15, #18)

**Semptom:** `st_opportunity=UNKNOWN` neredeyse tüm checkpoint'larda görünüyor. Bu `OPPORTUNITY_EVIDENCE_OR_CALIBRATION` waiting'ini sürekli tutuyor.

**Kök Neden:** `opportunity.py::assess_opportunity()` fonksiyonu.

```python
# opportunity.py, satır ~135
if targeting is None:
    return OpportunityAssessment(OpportunityState.UNKNOWN, ...)
if calibration is None:
    return OpportunityAssessment(OpportunityState.UNKNOWN, room, ...)
```

Calibration dosyası oluşturulmamışsa veya targeting snapshot yoksa, opportunity sonsuza dek UNKNOWN kalıyor. `assess_eligibility()` içinde:

```python
# eligibility.py, satır ~135
elif opportunity.state is OpportunityState.UNKNOWN:
    if armed:
        reasons.append("OPPORTUNITY_UNKNOWN_WHILE_ARMED")
    else:
        waiting.append("OPPORTUNITY_EVIDENCE_OR_CALIBRATION")
```

**Neden Sorun Yaratıyor:** Timing READY olsa bile, timing=READY değilken opportunity=UNKNOWN sürekli waiting üretiyor. Timing penceresi kaçtığında (P9) bu ikili kilit çözülmez hale geliyor.

**Düzeltme:**
- Calibration yoksa ve `room_atr` gözlemleniyorsa, bir default fallback sınıf üret (örn. MODERATE)
- Veya `targeting is None` durumunda UNKNOWN yerine COMPRESSED gibi conservative bir default kullan

---

### P3 — LT Otorite Beklemesi Hareketleri Tamamen Blokluyor (Move #2: +28.99%)

**Semptom:** 20 checkpoint'in tamamında `LT: UNRESOLVED/UNRESOLVED` → `action=WAIT` → `waiting=LONG_TERM_STRUCTURAL_AUTHORITY_TO_RESOLVE`.

**Kök Neden:** `arbiter.py::arbitrate_entry_scenarios()`.

```python
# arbiter.py, satır ~115
if long_term.presence is ScenarioPresence.UNKNOWN:
    ...
    unsafe = reason in {
        ScenarioUnknownReason.DATA_UNAVAILABLE,
        ScenarioUnknownReason.STRUCTURE_UNRESOLVED,
        ScenarioUnknownReason.NONE,
    }
    return EntryScenarioArbitration(
        ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION,
        ...
        waiting_for="LONG_TERM_STRUCTURAL_AUTHORITY_TO_RESOLVE"
    )
```

LT UNKNOWN iken ST henüz QUALIFIED değilse, sistem **tamamen** bloke ediliyor. ST QUALIFIED olsa bile yalnız `STRUCTURE_UNRESOLVED` reason'u ile izin veriyor.

**Neden Sorun Yaratıyor:** +29%'luk bir hareket tamamen kaçırılıyor. 1D yapı henüz resolve olmamışken 1H setup tamamen sağlam olabilir.

**Düzeltme:**
- LT UNKNOWN + ST PRESENT/DEVELOPING durumunda, ST'yi suppress etmek yerine "conditional ST fallback" mekanizması ekle
- Mevcut mimari "LT context/risk otoritesi" prensibini korurken, LT unresolved olduğunda ST'nin bağımsız trade üretebilmesine izin vermelidir (arbiter zaten ST_QUALIFIED + LT_UNKNOWN için bunu yapıyor, ama ST henüz QUALIFIED değilse yapmıyor)
- ST QUALIFIED olmamasının sebebi genellikle P2 (opportunity) ve P4 (timing) kaynaklı — dolaylı çözüm

---

### P4 — Execution Event Nadirliği: BUY Sinyali Çok Seyrek

**Semptom:** 20 hareketin yalnız birinde READY görülmüş (Move #3 +4%, Move #12 +10-12%, Move #16 +1% ve +7%), ama hiç BUY görülmemiş (audit scripti execution_event=None ile çalıştırıyor, bu nedenle BUY üretilemiyor).

**Kök Neden:** `execution_detect.py::detect_1h_execution_events()`.

```python
# execution_detect.py, satır ~155
if (seen_previous
    and phase in _CONFIRMED_PATTERN_PHASES
    and previous_phase not in _CONFIRMED_PATTERN_PHASES):
```

Execution event yalnız 1H pattern phase `BREAK_CONFIRMED` veya `RETEST_HELD`'e geçiş yaptığında üretiliyor. Bu geçiş:
1. Önceki phase'in confirmed OLMAMASI gerekiyor
2. Bir kere confirmed olduktan sonra yeni bir transition gerekiyor

Hızlı yükselişlerde pattern zaten confirmed durumda olabilir, yeni bir transition gerçekleşmeyebilir.

**Neden Sorun Yaratıyor:** Sistem READY üretebilir ama execution event olmadan BUY üretemiyor. Bu mimari bir safety feature (her READY'de otomatik BUY yapmıyor), ama execution event nadirliği gerçekte çok az trade anlamına geliyor.

**Düzeltme:**
- `readiness_execution_proxy=True` parametresi lifecycle_replay'de mevcut — audit/backtest bu modu kullanabilir
- Alternatif: Reaction CONFIRMED + timing READY birleştiğinde synthetic execution event üretme mekanizması
- Veya BOS event'i (şu an diagnostic) executable hale getirme (mimari tartışmalı)

---

### P5 — Timing Penceresi Kaybı (Move #5, #16)

**Semptom:** Timing READY'ye ulaşıyor ama diğer kapılar (opportunity, counter-LT) bekliyor. Bekleme uzadıkça timing FAILED veya DEVELOPING'e düşüyor.

**Kök Neden:** `timing.py::assess_timing()` → `assess_setup_trigger()`.

```python
# timing.py, satır ~220
if setup.state is SetupTriggerState.FAILED:
    return TimingAssessment(TimingState.FAILED, ...)
```

Setup trigger FAILED olduğunda (reaction failed veya pattern failed), timing FAILED oluyor. Bu doğal bir davranış ama counter-LT veya opportunity beklemesi sırasında reaction/pattern deteriorate olursa timing penceresi kayboluyor.

**Neden Sorun Yaratıyor:** Fiyat yükselmeye devam ederken sistem "zamanlama penceresi kapandı" diyor. Gerçek hayatta yükseliş devam ediyorsa timing hâlâ geçerli olmalı.

**Düzeltme:**
- Fiyat halen yapısal LONG tezini destekliyorsa (ST LONG/INTACT), timing FAILED yerine timing EARLY/DEVELOPING üret
- Veya timing decay mekanizması ekle: FAILED sonrası yeni bir reaction oluştuğunda hızlı recovery

---

### P6 — Chase/Extension Guard Yok (Mimari Eksiklik)

**Semptom:** Sistem +35% noktasında bile BUY üretebilir (teorik olarak). `TimingState.EXTENDED` enum'da var ama **asla üretilmiyor**.

```python
# timing.py, satır ~266
EXTENDED is part of the public contract but intentionally not emitted in v1;
no uncalibrated ATR/age threshold is introduced here.
```

**Kök Neden:** Timing modülünde EXTENDED state'i bilinçli olarak üretilmiyor. Entry kararında "bu hareket zaten çok ilerledi" kontrolü yok.

**Neden Sorun Yaratıyor:** Hedef kısa vadeli sermaye döngüsü ve gecikmiş/kovalanmış girişlerden kaçınmak. Ama bu guard olmadan sistem tüm kapılar hizalandığında geç bir BUY üretebilir.

**Düzeltme:**
- Move start'tan bu yana geçen yüzde ve zaman bazlı bir EXTENDED guard ekle
- `assess_timing()` içinde: fiyat move_start'tan X% yukarıdaysa ve N bar geçmişse, EXTENDED üret
- Bu calibration gerektirir ama minimum ATR-based threshold kullanılabilir

---

### P7 — ST Transition (EARLY_TRANSITION) Çok Sert Koşullar Gerektiriyor

**Semptom:** Move #4, #5, #10, #17, #18'de ST SHORT/INTACT iken fiyat yükseliyor. EARLY_TRANSITION scenario'ya geçiş çok nadir.

**Kök Neden:** `st_transition.py::assess_st_long_transition()`.

```python
# st_transition.py, satır ~235
stabil_led_strong = bool(
    stabil.recovery_confirmed
    and current_bullish_choch
    and reaction.confirmation_present
)
structure_led_strong = bool(
    canonical_transition_up
    and current_bullish_choch
    and reaction.confirmation_present
    and not stabil.opposes_early_long
)
strong = bool(not blockers and (stabil_led_strong or structure_led_strong))
```

STRONG (can_own_trade_thesis=True) için **3 koşulun hepsi** gerekiyor. Hızlı reversal'larda bu üçlü nadiren aynı anda mevcut olur.

**Neden Sorun Yaratıyor:** EARLY_TRANSITION scenario PRESENT olduğunda bazı gate'ler yumuşatılıyor (permission reconcile). Ama STRONG nadiren oluştuğu için bu avantaj kullanılamıyor.

**Düzeltme:**
- `stabil.recovery_developing + current_bullish_choch` veya `canonical_transition_up + reaction.developing_present` gibi daha gevşek STRONG koşulları ekle
- Veya DEVELOPING state'inin de `can_own_trade_thesis` üretmesine izin ver (daha zayıf overlay ile)

---

### P8 — Structural Transition Resolution Çok Geç (Move #12, #15)

**Semptom:** `STRUCTURAL_TRANSITION_TO_RESOLVE` waiting'i +10%'a kadar sürüyor.

**Kök Neden:** `eligibility.py::assess_eligibility()`.

```python
# eligibility.py, satır ~125
if structural.thesis_state is ThesisState.TRANSITIONING:
    waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")
```

Structure TRANSITIONING iken eligibility her zaman WAITING. Structure'ın kendi transition'ı (CHOCH/BOS ile INTACT olması) 1H'ta zaman alıyor.

**Neden Sorun Yaratıyor:** Fiyat hızla yükselirken yapı henüz transition'ı tamamlamamışsa, sistem bekliyor.

**Düzeltme:**
- Scenario EARLY_TRANSITION ve stabil recovery_confirmed ise transition bekleme koşulunu kaldır (ST_TRANSITION override)
- Veya TRANSITIONING + reaction CONFIRMED durumunda soft override

---

### P9 — Exit Mekanizması Yeterince Erken Değil (SELL Tarafı)

**Semptom:** Move #1 gibi büyük hareketlerde sistem pozisyonu tutup HOLD üretiyor. Bu doğru (pozisyon kârlı). Ama pozisyon tersine döndüğünde exit yeterince hızlı mı?

**Kök Neden:** `exit.py::refine_short_term_exit_with_stabil()` ve `trade_exit.py::assess_long_position_exit()`.

```python
# exit.py, satır ~130-150
# Stabil sadece EXIT_WATCH üretebilir, EXIT_READY üretemez
if stabil.breakdown_confirmed:
    return LongExitAssessment(ExitStage.EXIT_WATCH, ...)  # EXIT_READY değil!
```

```python
# trade_exit.py - ST exit için:
# Sadece ST INVALIDATED, ST SHORT/INTACT, veya ST TRANSITIONING→SHORT durumunda EXIT_READY
```

1H Structure tamamen SHORT/INTACT olana kadar exit armed olmuyor. Stabil breakdown bile olsa yalnızca EXIT_WATCH.

**Neden Sorun Yaratıyor:** ST structure deterioration beklenirken fiyat önemli ölçüde düşebilir. Exit penceresi daralır.

**Düzeltme:**
- `refine_short_term_exit_with_stabil()`: Stabil BREAKDOWN_CONFIRMED + ST direction SHORT (henüz INTACT değilse bile) → EXIT_READY yükseltmesi
- Veya persistence-based exit: 3 ardışık EXIT_WATCH + Stabil breakdown → EXIT_READY (mevcut `exit_ready_persistence_bars` mekanizması zaten var ama EXIT_READY için çalışıyor, EXIT_WATCH için değil)

---

### P10 — HOLD Reason'ları Audit Çıktısında Yanıltıcı Görünüyor

**Semptom:** Audit'te birçok MOVE'da HOLD görülüyor ama bu "sistem bir şey yapmıyor" değil — "pozisyon açık ve exit gereksiz" demek.

**Kök Neden:** `exit.py::compose_position_exit_decision()`.

```python
# exit.py, satır ~290
action = DecisionAction.SELL if execution.state is ExitExecutionState.CONFIRMED else DecisionAction.HOLD
```

Lifecycle OPEN iken entry bastırılıyor, exit decision üretiliyor. Exit armed değilse HOLD üretiliyor.

**Bu Bir Bug Değil:** Bu doğru mimari davranış. Ama audit raporunda "action=HOLD" yanıltıcı görünüyor. Kullanıcı "sistem neden HOLD diyor, neden BUY demiyor?" diye sorabilir.

**Düzeltme:** Audit raporlama katmanında lifecycle state'ini de göster (FLAT vs OPEN).

---

## 2. Her Problem İçin Kod Seviyesi Kök Neden

| # | Problem | Dosya | Fonksiyon | Koşul |
|---|---------|-------|-----------|-------|
| P1 | Counter-LT block | `engine.py` | `_apply_counter_lt_st_risk()` | `opportunity.state not in {MODERATE, AMPLE}` |
| P2 | Opportunity=UNKNOWN | `opportunity.py` | `assess_opportunity()` | `calibration is None` veya `targeting is None` |
| P3 | LT unresolved block | `arbiter.py` | `arbitrate_entry_scenarios()` | `LT=UNKNOWN and ST not QUALIFIED` |
| P4 | Execution event rarity | `execution_detect.py` | `_detect_pattern_events()` | Phase transition required |
| P5 | Timing window loss | `timing.py` | `assess_setup_trigger()` | Reaction failed while waiting |
| P6 | No chase guard | `timing.py` | `assess_timing()` | EXTENDED never emitted |
| P7 | ST transition too strict | `st_transition.py` | `assess_st_long_transition()` | 3/3 conditions for STRONG |
| P8 | Transition resolution delay | `eligibility.py` | `assess_eligibility()` | `thesis_state is TRANSITIONING` |
| P9 | Exit too late | `exit.py` | `refine_short_term_exit_with_stabil()` | Stabil cannot create EXIT_READY |
| P10 | HOLD misleading in audit | `exit.py` | `compose_position_exit_decision()` | Lifecycle OPEN → exit path |

---

## 3. Yanlış Çalışan veya Aşırı Güçlü Authority/Gate İlişkileri

### 3.1 — LT Authority Veto Çok Güçlü
- **Durum:** LT UNRESOLVED/UNKNOWN iken ST henüz QUALIFIED değilse tüm trade'ler bloke.
- **Otorite İlişkisi:** Arbiter LT UNKNOWN'ı absolute veto olarak kullanıyor.
- **Doğru mu?:** Kısmen. LT risk otoritesi olmalı ama ST QUALIFIED olmasa bile DEVELOPING ST setup'ları görmezden gelmemeli.
- **Öneri:** LT UNKNOWN + ST PRESENT/DEVELOPING → "ST_CONDITIONAL_FALLBACK" state'i ekle.

### 3.2 — Opportunity Gate Gereksiz Yere Bekleme Üretiyor
- **Durum:** Calibration yoksa opportunity sonsuza dek UNKNOWN. UNKNOWN + timing not READY → waiting.
- **Otorite İlişkisi:** Opportunity, eligibility içinde waiting üretiyor ama aslında direction ve setup sağlam olabilir.
- **Doğru mu?:** Hayır. Opportunity bilinmiyorsa bu "yok" demek değil, "bilinmiyor" demek. Bilinmeyen bir şey waiting üretmemeli.
- **Öneri:** `UNKNOWN + armed` durumunda soft reason (mevcut), `UNKNOWN + not armed` durumunda da soft reason olmalı (waiting yerine).

### 3.3 — Counter-LT Gate'lerin Birbiriyle Etkileşimi
- **Durum:** Counter-LT durumunda timing, opportunity, conflict hepsi ayrı gate olarak kontrol ediliyor.
- **Otorite İlişkisi:** `_apply_counter_lt_st_risk()` eligibility'yi override ediyor ama kendi waiting'lerini ekliyor.
- **Doğru mu?:** Gate'lerin birbiriyle çarpımı çok fazla bekleme üretiyor. Timing=READY iken opportunity=UNKNOWN olması normal olabilir.
- **Öneri:** Counter-LT gate'lerini azalt — timing=READY tek başına yeterli olmalı (opportunity ve conflict soft reason olarak kalmalı).

---

## 4. BUY Tarafındaki Ana Darboğazlar

### Sıralı Darboğaz Zinciri:

```
1. LT Otorite Beklemesi (P3)
   ↓ eğer geçilirse
2. ST Scenario QUALIFIED Olmaması (P7: EARLY_TRANSITION too strict)
   ↓ eğer geçilirse  
3. Opportunity=UNKNOWN (P2)
   ↓ eğer geçilirse
4. Counter-LT Ek Gate'leri (P1)
   ↓ eğer geçilirse
5. Timing Penceresi Kaybı (P5)
   ↓ eğer geçilirse
6. Execution Event Yok (P4)
   ↓ eğer geçilirse
7. Chase Guard Yok — geç BUY riski (P6)
```

**En Kritik Darboğaz:** P1+P2 birleşimi. Counter-LT durumunda opportunity=UNKNOWN + timing not READY = sonsuz WAIT.

**İkinci Kritik Darboğaz:** P4 (execution event). Tüm gate'ler geçilse bile, 1H pattern transition olmadan BUY üretilmiyor.

---

## 5. SELL Tarafındaki Ana Darboğazlar

### SELL Zinciri:
```
1. ST Structure deterioration bekleniyor (INTACT → INVALIDATED/SHORT)
   ↓
2. Stabil sadece EXIT_WATCH üretebilir, EXIT_READY değil
   ↓
3. EXIT_READY olduktan sonra execution event bekleniyor
   ↓
4. 3 bar persistence ile otomatik exit (mevcut mekanizma)
```

**Ana Sorun:** 1H Structure deterioration zaman alıyor. Bu süre zarfında fiyat düşmeye devam edebilir. Stabil breakdown erken uyarı veriyor ama exit arming'i tetikleyemiyor.

**İkinci Sorun:** LT entry pozisyonlarında `assess_long_position_exit()` LT INVALIDATED veya LT SHORT/INTACT bekliyor. LT TRANSITIONING → yalnızca EXIT_WATCH.

---

## 6. Causal/State-Machine Riskleri

### 6.1 — Causal Safety: TEMİZ ✅
- `CausalTimelineReducer` (causal_reducer.py) bar'ları `(available_at, timeframe, bar_index)` sırasına göre tüketiyor.
- `assess_execution_trigger()` future-unavailable ref'leri ve future-observed event'leri reddediyor.
- `_decision_structure_projection()` DATA_LIMITED → VALID promotion yapıyor ama bu Decision-only, native domain değişmiyor.
- Execution detect sadece phase transition'larda event üretiyor (lookahead yok).

### 6.2 — State Machine Tutarlılığı: TEMİZ ✅
- `TradeLifecycleState` frozen dataclass, FLAT↔OPEN geçişleri doğru kontrol ediliyor.
- `transition_trade_lifecycle()` OPEN iken BUY'ı bastırıyor, FLAT iken SELL'i bastırıyor.
- Exit persistence mekanizması (`exit_ready_persistence_bars`) causal — sadece geçmiş bar'ları sayıyor.

### 6.3 — Potansiyel Risk: Frozen Cache Invalidation
Audit çıktısında `FROZEN_CACHE: HIT_REBOUND_CONTENT_IDENTITY` ve `AUDIT_CACHE: HIT` görünüyor. Kod değişikliğinden sonra cache invalidation doğru çalışıyor mu? `_decision_code_fingerprint()` tüm decision/*.py dosyalarını hash'liyor — bu güvenli görünüyor.

### 6.4 — Potansiyel Risk: `_decision_structure_projection()` Quality Override
```python
# engine.py, satır ~115
# DATA_LIMITED → VALID promotion for price-only Structure
```
Bu promotion native domain'i değiştirmiyor ama Decision'a VALID olarak giriyor. Eğer native engine DATA_LIMITED'i doğru bir nedenden veriyorsa (örn. yetersiz bar sayısı), bu override yanlış pozitif üretebilir.

---

## 7. Testlerde Eksik Kalan Kritik Davranışlar

### 7.1 — Counter-LT + Opportunity=UNKNOWN Senaryosu
Mevcut testlerde counter-LT + timing=READY + opportunity=MODERATE test edilmiş olabilir. Ama **counter-LT + timing=DEVELOPING + opportunity=UNKNOWN** (en yaygın gerçek dünya senaryosu) test ediliyor mu?

### 7.2 — Timing Penceresi Kaybı (READY → FAILED During Wait)
Bir setup timing=READY'ye ulaşıp, eligibility bekleme sırasında timing=FAILED'e düşüyor. Bu lifecycle senaryosu entegrasyon testlerinde var mı?

### 7.3 — Chase/Extension Guard Testi
EXTENDED state'i üretilmediği için test de yok. Sistem +30% move'da hala BUY üretebilir mi?

### 7.4 — Exit Persistence + Stabil Breakdown
Stabil breakdown + ST henüz SHORT değilken exit persistence mekanizması doğru çalışıyor mu?

### 7.5 — LT UNKNOWN + ST QUALIFIED Path
Arbiter'ın `st_qualified + LT UNKNOWN` path'i test ediliyor ama `st_qualified` olması için gereken opportunity/timing koşulları gerçek verilerde nadiren oluşuyor. Bu chain'i test eden entegrasyon testi var mı?

### 7.6 — Opportunity Calibration Yokluğu
Calibration=None ile tüm decision pipeline'ı test ediliyor mu? Production'da calibration yoksa sistem nasıl davranıyor?

---

## 8. Öncelik Sırasına Göre Düzeltme Planı

### Acil (Trade Üretimini Doğrudan Etkileyen)

| Öncelik | Problem | Düzeltme | Etki |
|---------|---------|----------|------|
| 1 | P1+P2: Counter-LT + Opportunity UNKNOWN | `_apply_counter_lt_st_risk()`: opportunity=UNKNOWN + timing=READY → soft reason | 5 hareket açılabilir |
| 2 | P4: Execution event nadirliği | `readiness_execution_proxy=True` backtest modu veya reaction CONFIRMED + timing READY → synthetic event | BUY sinyali üretilebilir |
| 3 | P7: ST transition strictness | `assess_st_long_transition()`: 2/3 koşul yeterli (stabil_led_strong veya structure_led_strong gevşetme) | EARLY_TRANSITION daha sık |

### Önemli (Mimari Doğruluk)

| Öncelik | Problem | Düzeltme | Etki |
|---------|---------|----------|------|
| 4 | P3: LT unresolved total block | Arbiter: LT UNKNOWN + ST DEVELOPING → conditional fallback | Move #2 gibi kayıplar azalır |
| 5 | P5: Timing window loss | Timing decay mekanizması: ST LONG/INTACT iken FAILED→EARLY recovery | Timing sürekliliği |
| 6 | P8: Transition delay | EARLY_TRANSITION + stabil recovery → TRANSITIONING bekleme kaldır | Move #12, #15 |

### İyileştirme (Safety + Quality)

| Öncelik | Problem | Düzeltme | Etki |
|---------|---------|----------|------|
| 7 | P6: Chase guard | `assess_timing()`: move_start'tan %X veya N bar → EXTENDED | Geç giriş engellenir |
| 8 | P9: Exit speed | Stabil BREAKDOWN_CONFIRMED + ST any deterioration → EXIT_READY | Sermaye koruması |
| 9 | P10: Audit clarity | Audit raporunda lifecycle state göster | Okunabilirlik |

---

## 9. Düzeltilmemesi Gereken, Şu An Doğru Çalışan Parçalar

### 9.1 — HOLD Pozisyon Koruması ✅
Lifecycle OPEN iken entry'nin bastırılması doğru. Tekrarlanan BUY sinyalleri pozisyon yönetimini bozar.

### 9.2 — Causal Safety Chain ✅
`CausalTimelineReducer` → `FactRef.is_available_at()` → `assess_execution_trigger()` zinciri lookahead'i tamamen engelliyor.

### 9.3 — Structure Owns Direction ✅
Supporting domain'lar (Stabil, Volume, Pattern) direction/thesis değiştiremiyor. Bu mimari kural kesin uygulanıyor.

### 9.4 — Conflict Assessment Transparency ✅
`assess_conflict()` 3 bağımsız family (reaction, participation, environment) üzerinden şeffaf hesaplanıyor. HIGH için 2+ MATERIAL gerekiyor.

### 9.5 — Lifecycle State Machine ✅
FLAT↔OPEN geçişleri, exit persistence, metadata dondurma — hepsi doğru ve tutarlı.

### 9.6 — Permission System ✅
`PermissionEnvelope` gate_state/scope/permitted_side ayrımı doğru. 30m veto authority yok.

### 9.7 — ST Transition Overlay Mimarisi ✅
Native Structure mutate edilmiyor, Decision-only overlay oluşturuluyor. Bu doğru bir separation of concerns.

### 9.8 — Exit Execution Event Validation ✅
`_validate_exit_event()` timestamps, side, timeframe, ref availability kontrolü yapıyor. Future leakage yok.

### 9.9 — Opportunity Semantics Separation ✅
`_target_semantics()` reaction-only zone'ları soft context olarak ayırıyor, liquidity/SR zone'ları hard constraint olarak tutuyor. Doğru ekonomik ayrım.

### 9.10 — Eligibility Composition ✅
`assess_eligibility()` hard gate'leri (BLOCKED) ve soft gate'leri (WAITING) doğru ayırıyor. BLOCKED asla WAITING'e düşmüyor.

---

## 10. Emin Olmadığım Noktalar ve Doğrulamak İçin Gereken Ek Audit'ler

### 10.1 — Opportunity Calibration Var mı?
Audit çıktısında opportunity=UNKNOWN görülüyor ama bu calibration dosyasının yokluğundan mı yoksa targeting snapshot yokluğundan mı kaynaklanıyor?

**Gereken:** `scripts/build_opportunity_calibration.py` çalıştırılmış mı? Calibration JSON dosyası mevcut mu?

### 10.2 — Reaction Zone Relevance Policy Kalibrasyonu
`ReactionRelevancePolicy(max_age_bars=50, max_distance_atr=5.0)` default değerleri ASELS için uygun mu? Çok dar relevance policy, reaction CONFIRMED'i engelliyor olabilir.

**Gereken:** Reaction zone'larının age ve distance dağılımı.

### 10.3 — Pattern Compression 1H State Dağılımı
Execution event'lerin nadirliği 1H pattern behavior state distribution'ından kaynaklanıyor olabilir. ASELS'te 1H pattern'lar ne sıklıkla BREAK_CONFIRMED'e ulaşıyor?

**Gereken:** 1H pattern phase timeline dump.

### 10.4 — Stabil Support Projection Kalitesi
`stabil_quality=DATA_LIMITED` ve `quality=UNAVAILABLE` sık görülüyor. Stabil engine yeterli veri alıyor mu? Daily support/refistance zone'ları doğru hesaplanıyor mu?

**Gereken:** Stabil support projection detayları ve underlying daily data quality.

### 10.5 — Targeting Snapshot Varlığı
`targeting is None` durumunda opportunity=UNKNOWN üretiyor. Targeting engine çalışıyor mu? Target cluster'lar oluşuyor mu?

**Gereken:** Targeting snapshot dump'ı — `snapshot.targeting` ne içeriyor?

### 10.6 — LT 1D Structure State Timeline
Move #2'de LT tamamen UNRESOLVED. 1D structure engine hangi state'i üretiyor? BULLISH/BEARISH/NEUTRAL/TRANSITION_UP? Bu state neden UNRESOLVED olarak kalıyor?

**Gereken:** 1D structure timeline ve external scope state distribution.

### 10.7 — Backtest readines_execution_proxy Modu Sonuçları
`readiness_execution_proxy=True` ile backtest çalıştırıldığında ne kadar BUY üretiyor? Bu proxy causal safety'yi bozuyor mu?

**Gereken:** Proxy-enabled backtest sonuçları ve karşılaştırma.

### 10.8 — 30m Execution Channel Katkısı
30m execution events (`detect_30m_execution_events`) lifecycle replay'e dahil mi? Dahilse ne kadar ek BUY/SELL üretiyor?

**Gereken:** 30m execution event counts ve lifecycle impact.

### 10.9 — Participation Behavior Projection
`PARTICIPATION_OPPOSING` veya `UNSUPPORTED_BREAK` conflict üretiyor mu? ASELS'te participation behavior ne sıklıkla opposing gösteriyor?

**Gereken:** Participation state distribution timeline.

### 10.10 — Exit Ready Persistence Mekanizması
`exit_ready_persistence_bars=3` doğru kalibre edilmiş mi? 3 bar (3 saat) yeterli mi yoksa çok mu kısa/uzun?

**Gereken:** EXIT_READY → persistence → SELL zincirinin historical örnekleri.

---

## Ek: Mimari Değerlendirme

### Genel Mimari: Güçlü Ama Aşırı Teyitli

Sistemin mimarisi temelden sağlam:
- Causal safety kesinlikle uygulanıyor
- Structure owns direction kuralı korunuyor
- LT/ST ayrımı doğru
- State machine tutarlı
- Lineage tracking mevcut

**Ana mimari eleştiri:** Sistem **aşırı teyitli** (over-confirmation). Her katman bağımsız olarak "hazırım" demeden trade üretilmiyor. Bu güvenlik sağlar ama fırsat maliyeti çok yüksek.

**Alternatif Mimari Yaklaşım:**
- **Tiered confidence:** Her gate'in tamamlanması yerine, toplam confidence skoru hesaplanabilir. Yüksek confidence = BUY, orta = WATCH, düşük = NO_TRADE.
- **Adaptive gating:** Hareket hızı arttıkça bazı gate'ler soft override edilebilir (hızlı hareketlerde reaction/pattern zaten hızlı oluşur).
- **Probabilistic readiness:** Deterministik gate'ler yerine Bayesian readiness: "bu kadar evidence var, yeterli mi?" sorusu.

Bu alternatifler mevcut mimariyi değiştirir ve daha fazla kalibrasyon gerektirir. Mevcut v1 deterministic yaklaşımı doğru bir başlangıç noktası ama P1-P9 düzeltmeleri olmadan production-ready değil.

---

## Ek: 20 Hareket Özeti Tablo

| # | Move% | Ana Aksiyon | Ana Blokaj | Kök Neden |
|---|-------|-------------|------------|-----------|
| 1 | +35.76 | HOLD | Pozisyon açık | ✅ Doğru |
| 2 | +28.99 | WAIT | LT UNRESOLVED | P3 |
| 3 | +21.16 | READY→HOLD | +4% READY sonra pozisyon açıldı | ✅ Kısmen doğru |
| 4 | +20.07 | NO_TRADE→WAIT | Counter-LT + Stabil | P1, P7 |
| 5 | +17.52 | WAIT | Counter-LT + Opportunity UNKNOWN | P1, P2 |
| 6 | +16.00 | HOLD | Pozisyon açık | ✅ Doğru |
| 7 | +15.31 | HOLD | Pozisyon açık | ✅ Doğru |
| 8 | +14.24 | HOLD | Pozisyon açık | ✅ Doğru |
| 9 | +14.06 | WAIT | ST SHORT + LT priority | P7, P3 |
| 10 | +13.57 | WAIT | Counter-LT | P1 |
| 11 | +13.52 | HOLD | Pozisyon açık | ✅ Doğru |
| 12 | +13.00 | WAIT→READY | Transition delay +10-12% | P8 |
| 13 | +11.47 | NO_TRADE→WAIT | Conflict high | Doğal block |
| 14 | +10.06 | HOLD | Pozisyon açık | ✅ Doğru |
| 15 | +9.79 | WAIT | Transition unresolved | P8 |
| 16 | +8.98 | READY→WAIT→READY | Intermittent | P5 |
| 17 | +8.97 | NO_TRADE→WAIT | Counter-LT | P1, P7 |
| 18 | +7.91 | WAIT | Counter-LT | P1 |
| 19 | +7.69 | HOLD | Pozisyon açık | ✅ Doğru |
| 20 | +7.22 | NOT_SEEN | Snapshot yok | Veri sorunu |

**Özet:**
- 9/20 = Pozisyon açık (HOLD) → ✅ Doğru davranış
- 1/20 = READY + pozisyon açıldı → ✅ Doğru
- 1/20 = Veri yok → ⚠️ Altyapı
- 9/20 = Kaçırılan veya geç yakalanan → 🔴 P1-P9 problemleri

**Kaçırılan/Geç 9 Hareketin Kök Neden Dağılımı:**
- Counter-LT block: 5 hareket (P1+P2)
- LT unresolved: 1 hareket (P3)
- ST transition strict: 3 hareket (P7)
- Transition delay: 2 hareket (P8)
- Timing window: 1 hareket (P5)
- (Birden fazla problem aynı hareketi etkileyebilir)
