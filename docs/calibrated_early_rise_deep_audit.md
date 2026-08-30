# Calibrated Early-Rise Deep Audit — "One More Confirmation" Hypothesis

**Date:** 2026-08-30
**Branch:** `arena/01a053d1-financial-dashboard`
**Calibration:** `none<=0.739ATR; compressed<=1.659ATR; moderate<=2.818ATR; ample>2.818ATR`
**Symbol:** ASELS | **Moves:** 20 | **Min rise:** +7%

---

## Executive Summary

The "one more confirmation" hypothesis is **confirmed**. The system requests the same underlying economic/technical evidence under 5–7 different names across 4 independent layers. This creates a cascading gate chain where each layer independently blocks on semantically identical conditions, making BUY signals extremely rare during fast moves.

The single most damaging mechanism is **OPPORTUNITY_NONE as a hard block** when the nearest liquidity magnet is within 0.74 ATR — which happens precisely when price is breaking through that level during a strong move.

---

## 1. En Sık Tekrar Eden 5 Davranış

### D1 — OPPORTUNITY_NONE Hard Block (9/20 harekette)
**Görülen hareketler:** #4(+10-13%), #9(+3%,+5-6%), #11(+5-8%), #12(+4-6%,+10-12%), #13(+8-10%), #16(+1-4→NO_TRADE→WAIT), #17(+3-6%), #18(+4-6%), #19(+7%)

Fiyat bir liquidity magnet seviyesine yaklaştığında `room_atr` 0.74'ün altına düşüyor → `OpportunityState.NONE` → `eligibility.BLOCKED` → `scenario.BLOCKED` → `NO_TRADE`.

**Kök:** `eligibility.py:100-101` + `opportunity.py:190` + `_target_semantics()` LIQUIDITY_MAGNET → `hard_room_constraint=True`

```
room=0.211ATR → NONE → blockers=["OPPORTUNITY_NONE"] → BLOCKED
room=0.098ATR → NONE → blockers=["OPPORTUNITY_NONE"] → BLOCKED  
room=0.048ATR → NONE → blockers=["OPPORTUNITY_NONE"] → BLOCKED
```

Fiyat bu seviyeyi kırdığında bir sonraki checkpoint'te yeni bir target seçiliyor ve room tekrar MODERATE/AMPLE olabiliyor. Ama o ara checkpoint'te scenario BLOCKED olduğu için QUALIFIED olamıyor.

### D2 — TARGET_PATH_TO_RESOLVE (6/20 harekette)
**Görülen hareketler:** #4(+7-16%), #10(+1%,+6-10%), #12(+1-9%), #15(+1-7%), #16(+5%), #18(+1-3%)

`target_path.status is not TargetPathStatus.READY` → `waiting.append("TARGET_PATH_TO_RESOLVE")` (scenario.py:252-253).

**Kök:** `target_path.py:728`: `status = TargetPathStatus.READY if nodes else TargetPathStatus.NO_OBSERVED_PATH`. Target path node'ları liquidity/SR/reaction zone'larından oluşuyor. Eğer bu zone'lar yoksa veya fiyat hepsini geçmişse, path NO_OBSERVED_PATH → waiting.

Bu, opportunity ile **aynı ekonomik kanıtı** farklı bir isimle istiyor: "önünde bir hedef var mı?"

### D3 — STRUCTURAL_TRANSITION_TO_RESOLVE (4/20 harekette)
**Görülen hareketler:** #12(+1-9%), #15(+1-7%), #19(+4-6%), #1(+1-2%)

**İki katmanda tekrarlanıyor:**
- `eligibility.py:119`: `if structural.thesis_state is ThesisState.TRANSITIONING: waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")`
- `scenario.py:259-260`: `if structural.thesis_state is ThesisState.TRANSITIONING: waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")`

Aynı koşul, aynı reason, iki farklı dosyada. Entry decision'a ulaştığında ikisi birleşiyor ama semantik olarak tek bir gate.

### D4 — CANONICAL_STRUCTURAL_FOLLOW_THROUGH + PERMISSION_SCOPE_SIDE_TO_RECONCILE (4/20 harekette)
**Görülen hareketler:** #12(+1-9%), #15(+1-7%), #19(+4-6%)

`permissions.py:145`: Reversal CANDIDATE durumunda `waiting_for=("CANONICAL_STRUCTURAL_FOLLOW_THROUGH",)`. Bu permission envelope → eligibility → scenario zincirine akıyor.

Bu da STRUCTURAL_TRANSITION_TO_RESOLVE ile **aynı şeyi** istiyor: "yapısal geçiş tamamlansın."

### D5 — COUNTER_LT_ST Gate Chain (5/20 harekette)
**Görülen hareketler:** #4(+7-16%), #10(+3-10%), #16(+5%), #18(+2-3%), #9(+7-12%)

`engine.py:321-327`:
```python
if timing.state is not TimingState.READY:
    waiting.append("COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP")
if opportunity.state not in {OpportunityState.MODERATE, OpportunityState.AMPLE}:
    waiting.append("COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM")
```

Bu gate'ler eligibility'nin kendi waiting'lerine **ek** olarak ekleniyor. Timing zaten FAILED ise timing kendi waiting'ini üretiyor (`NEW_SETUP_PATH`). Counter-LT gate'i bunu `COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP` olarak tekrarlıyor.

---

## 2. Bu Davranışların Hareket Dağılımı

| Davranış | Hareketler | Sıklık |
|----------|-----------|--------|
| D1: OPPORTUNITY_NONE hard block | #4,#9,#11,#12,#13,#16,#17,#18,#19 | 9/20 |
| D2: TARGET_PATH_TO_RESOLVE | #4,#10,#12,#15,#16,#18 | 6/20 |
| D3: STRUCTURAL_TRANSITION double-count | #1,#12,#15,#19 | 4/20 |
| D4: CANONICAL_FOLLOW_THROUGH | #12,#15,#19 | 3/20 |
| D5: COUNTER_LT_ST chain | #4,#9,#10,#16,#18 | 5/20 |
| Position already open (HOLD) | #1,#5(≥+2%),#6,#7,#8,#11,#14,#17 | 8/20 |
| READY produced | #3(+4%), #5(+1%), #10(+5%) | 3/20 |
| LT UNRESOLVED total block | #2 | 1/20 |
| No data | #20 | 1/20 |

**Net sonuç:** 20 hareketin 3'ünde READY üretildi (ve lifecycle BUY'a dönüştü). 8'inde pozisyon zaten açıktı (HOLD doğru). 9'unda BUY hiç üretilmedi.

---

## 3. Aynı Teyidin Birden Fazla Katmanda Tekrarlandığı Yerler

### 3.1 — "Setup Olgunluğu" 4 Farklı İsimle İsteniyor

| Katman | Dosya | Reason | Anlam |
|--------|-------|--------|-------|
| Timing | timing.py:322 | `NEW_SETUP_PATH` | Setup başarısız, yenisini bekle |
| Timing | timing.py:331 | `SETUP_TRIGGER_CONFIRMATION` | Setup oluşuyor, onay bekle |
| Counter-LT | engine.py:323 | `COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP` | Timing READY değil |
| Scenario | scenario.py:253 | `TARGET_PATH_TO_RESOLVE` | Target path hazır değil |

Bunların hepsi aynı şeyi soruyor: **"1H setup yeterince olgun mu?"** Ama her biri bağımsız gate olarak çalışıyor.

### 3.2 — "Yapısal Geçiş Tamamlandı mı?" 3 Farklı İsimle İsteniyor

| Katman | Dosya | Reason | Anlam |
|--------|-------|--------|-------|
| Eligibility | eligibility.py:119 | `STRUCTURAL_TRANSITION_TO_RESOLVE` | Thesis TRANSITIONING |
| Scenario | scenario.py:259 | `STRUCTURAL_TRANSITION_TO_RESOLVE` | Thesis TRANSITIONING (aynı!) |
| Permission | permissions.py:145 | `CANONICAL_STRUCTURAL_FOLLOW_THROUGH` | Reversal CANDIDATE |

Üçü de aynı koşulu kontrol ediyor: yapısal geçiş tamamlandı mı?

### 3.3 — "Yukarıda Yeterli Alan Var mı?" 3 Farklı İsimle İsteniyor

| Katman | Dosya | Reason | Anlam |
|--------|-------|--------|-------|
| Eligibility | eligibility.py:101 | `OPPORTUNITY_NONE` (BLOCKER) | Room ≤ 0.74 ATR |
| Eligibility | eligibility.py:133 | `MORE_DIRECTIONAL_ROOM` | Opportunity COMPRESSED |
| Counter-LT | engine.py:325 | `COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM` | Opportunity MODERATE/AMPLE değil |
| Scenario | scenario.py:248 | `MORE_DIRECTIONAL_ROOM` | Opportunity NONE + hard constraint |

Dört farklı noktada "yukarıda alan var mı?" sorusu soruluyor.

---

## 4. Gerçek Kök Nedenler

### RN1 — Opportunity NONE Hard Block Tasarım Hatası

`_target_semantics()` LIQUIDITY_MAGNET hedefleri için `hard_room_constraint=True` döndürüyor. Bu, en yakın liquidity seviyesinin **gerçek bir kâr tavanı** olduğunu varsayıyor. Ama güçlü yükselişlerde fiyat bu seviyeleri **kırarak geçiyor**.

**Problem:** Targeting engine en yakın overhead cluster'ı seçiyor. Fiyat bu cluster'a yaklaştığında room → 0 → NONE → BLOCKED. Ama bu seviye bir "duvar" değil, bir "mıknatıs" — fiyat oraya çekilip geçebilir.

**Kod:** `opportunity.py:87-100` (`_target_semantics`) + `eligibility.py:100-101`

### RN2 — Target Path ve Opportunity Aynı Veriyi İki Kere İşliyor

`target_path.py` liquidity/SR/reaction zone'larından path node'ları oluşturuyor. `opportunity.py` aynı targeting snapshot'tan en yakın cluster'ı seçiyor. İkisi de "önünde ne var?" sorusunu soruyor ama farklı mekanizmalarla.

Target path boşsa → `TARGET_PATH_TO_RESOLVE`. Opportunity NONE ise → `OPPORTUNITY_NONE` blocker. İkisi aynı anda tetiklenebilir.

### RN3 — Waiting Reason'ları Katmanlar Arası Dedup Edilmiyor

`entry.py:compose_entry_decision()` sonunda `_dedup()` çağrılıyor ama bu sadece string düzeyinde dedup yapıyor. Semantik olarak aynı olan farklı isimler (`NEW_SETUP_PATH` vs `COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP` vs `SETUP_TRIGGER_CONFIRMATION`) ayrı reason'lar olarak kalıyor.

### RN4 — Opportunity Target Identity Çok Sık Değişiyor

Move #1'de 33 checkpoint'te 6+ farklı target ID görülüyor:
```
TC-ABOVE-9378cb229262 (2.004 ATR)
TC-ABOVE-7c155098ce2a (0.218 ATR) ← NONE!
TC-ABOVE-d3c2cac18481 (1.987 ATR)
TC-ABOVE-ccecfd18581a (0.732 ATR) ← NONE!
TC-ABOVE-ad4359a2cf2c (0.394 ATR) ← NONE!
```

Her target değiştiğinde room sıfırlanıyor. Fiyat bir liquidity seviyesini kırdığında, o seviye artık "ahead" değil, dolayısıyla yeni bir nearest target seçiliyor. Bu yeni target bazen çok yakın olabiliyor.

**Kök:** `opportunity.py:_target_for_side()` → `targeting.nearest_upside_target`. Bu her snapshot'ta yeniden hesaplanıyor. Sticky/hysteresis mekanizması yok.

### RN5 — Execution Event Nadirliği Hâlâ Geçerli

Calibration düzeltilmiş olsa bile, BUY için hâlâ `ExecutionTriggerState.CONFIRMED` gerekiyor. Bu yalnız 1H pattern phase transition'ında üretiliyor. Move #5'te +1%'de READY üretilmiş ama execution event olmadan BUY olmamış (lifecycle replay `readiness_execution_proxy` kullanmamışsa).

---

## 5. Kod Seviyesinde Sorumlu Dosya/Fonksiyonlar

| Dosya | Fonksiyon | Sorumluluk |
|-------|-----------|------------|
| `opportunity.py` | `_target_semantics()` | LIQUIDITY_MAGNET → hard_room_constraint=True |
| `opportunity.py` | `assess_opportunity()` | room ≤ none_max_atr → NONE |
| `eligibility.py` | `assess_eligibility()` | OPPORTUNITY_NONE → BLOCKED (hard gate) |
| `eligibility.py` | `assess_eligibility()` | STRUCTURAL_TRANSITIONING → waiting (duplicate) |
| `scenario.py` | `build_entry_scenario()` | STRUCTURAL_TRANSITIONING → waiting (duplicate) |
| `scenario.py` | `build_entry_scenario()` | target_path not READY → waiting |
| `scenario.py` | `build_entry_scenario()` | OPPORTUNITY_NONE → MORE_DIRECTIONAL_ROOM (duplicate) |
| `engine.py` | `_apply_counter_lt_st_risk()` | Counter-LT extra gates (duplicate of timing+opportunity) |
| `permissions.py` | `resolve_permission_axes()` | Reversal CANDIDATE → CANONICAL_FOLLOW_THROUGH |
| `target_path.py` | `build_target_path()` | NO_OBSERVED_PATH → status not READY |

---

## 6. Hangi Kurallar Gerçekten Gerekli

### Mutlaka Korunması Gerekenler:

1. **Causal safety** — `available_at > as_of` kontrolü. Asla gevşetilmemeli.
2. **Structure owns direction** — Supporting domain'lar direction/thesis değiştiremez.
3. **Execution event requirement** — Her READY'de otomatik BUY değil, taze bir event gerekli. (Ama event tanımı genişletilebilir.)
4. **STABIL_FOUNDATION_BROKEN hard block** — Stabil gerçekten broken ise yeni long açılmamalı.
5. **INDEPENDENT_FAMILY_CONFLICT_HIGH** — 2+ MATERIAL family = HIGH → BLOCKED. Doğru.
6. **Lifecycle OPEN iken entry bastırma** — Tekrarlanan BUY pozisyon yönetimini bozar.
7. **Future-unavailable ref rejection** — Execution event timestamp kontrolü.

### Gerektiğinde Düzeltilmesi Gerekenler:

8. **LT direction UNRESOLVED → total block** — ST QUALIFIED ise LT unresolved soft override edilebilir (arbiter zaten yapıyor, ama ST QUALIFIED olması zor).

---

## 7. Hangi Kurallar Fazla Güçlü veya Mükerrer

### Mükerrer (Aynı Kanıt, Farklı İsim):

| # | Kural Set | Tekrar Sayısı | Çözüm |
|---|-----------|---------------|-------|
| M1 | Setup olgunluğu | 4 katman | Timing tek otorite olmalı; counter-LT setup gate'i kaldırılmalı |
| M2 | Yapısal geçiş | 3 katman | Eligibility'deki duplicate kaldırılmalı; permission tek kaynak |
| M3 | Yukarı alan | 4 katman | Opportunity tek otorite olmalı; scenario ve counter-LT duplicate'leri kaldırılmalı |

### Fazla Güçlü:

| # | Kural | Neden Fazla Güçlü | Öneri |
|---|-------|-------------------|-------|
| F1 | OPPORTUNITY_NONE hard block | Fiyat liquidity seviyesini kırarken NONE üretiyor | LIQUIDITY_MAGNET için hard_room_constraint=False yapılmalı; veya NONE soft blocker olmalı |
| F2 | TARGET_PATH_TO_RESOLVE | Opportunity ile aynı veriyi işliyor | Target path waiting'i kaldırılmalı; opportunity yeterli |
| F3 | COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP | Timing zaten FAILED/DEVELOPING üretiyor | Counter-LT timing gate'i kaldırılmalı |
| F4 | COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM | Opportunity zaten COMPRESSED/NONE üretiyor | Counter-LT opportunity gate'i kaldırılmalı |

---

## 8. BUY Gecikmesini Azaltmak İçin En Temiz Mimari Çözüm

### Çözüm A: Gate Deduplication (Önerilen)

**Prensip:** Her ekonomik/teknik kanıt **tek bir otorite** tarafından değerlendirilmeli. Diğer katmanlar bu otoritenin sonucunu kullanmalı, tekrar değerlendirmemeli.

**Otorite Dağılımı:**
```
Setup Olgunluğu    → Timing (tek otorite)
Yapısal Geçiş      → Permission (tek otorite)  
Yukarı Alan        → Opportunity (tek otorite)
Target Path        → Opportunity ile birleştirilmeli
Counter-LT Risk    → Conflict'e taşınmalı (zaten conflict assessment var)
```

**Somut Değişiklikler:**

1. **`eligibility.py`**: `STRUCTURAL_TRANSITION_TO_RESOLVE` waiting'ini kaldır (permission zaten kontrol ediyor)
2. **`scenario.py`**: `STRUCTURAL_TRANSITION_TO_RESOLVE` waiting'ini kaldır
3. **`scenario.py`**: `TARGET_PATH_TO_RESOLVE` waiting'ini kaldır (opportunity yeterli)
4. **`scenario.py`**: `MORE_DIRECTIONAL_ROOM` waiting'ini kaldır (eligibility zaten BLOCKED üretiyor)
5. **`engine.py`**: `_apply_counter_lt_st_risk()` fonksiyonunu sadeleştir — sadece conflict gate'i kalmalı, timing ve opportunity gate'leri kaldırılmalı
6. **`opportunity.py`**: `_target_semantics()` — LIQUIDITY_MAGNET için `hard_room_constraint=False` yap (veya NONE soft blocker olsun)

### Çözüm B: Opportunity Hysteresis (Tamamlayıcı)

Target identity çok sık değişiyor. Bir target seçildiğinde, fiyat o target'ı geçene kadar (veya N bar boyunca) aynı target'ı kullan. Bu, room'un 0.2→2.0→0.4→NONE şeklinde salınmasını engeller.

**Somut:** `opportunity.py:assess_opportunity()` içinde sticky target mekanizması — önceki target hâlâ ahead ise ve room > none_max_atr ise, önceki target'ı kullan.

### Çözüm C: Execution Event Broadening (Tamamlayıcı)

Mevcut: Sadece 1H pattern phase transition → CONFIRMED.
Öneri: Reaction CONFIRMED + timing READY birleştiğinde synthetic execution event üret. Bu, pattern engine'in nadir transition'lerini beklemeden, reaction evidence yeterli olduğunda BUY üretilmesini sağlar.

**Somut:** `execution_detect.py` içinde reaction-based event detection ekle.

---

## 9. Yanlış Şekilde "Gevşetilmemesi" Gereken Güvenlik Kuralları

1. **OPPORTUNITY_NONE soft blocker yapılabilir ama tamamen kaldırılamaz.** Hiç alan yoksa (room ≤ 0.1 ATR) trade açılmamalı. Ama 0.74 ATR çok agresif bir eşik — bu calibrasyon problemi, mimari problem değil.

2. **Execution event requirement kaldırılamaz.** Her READY'de otomatik BUY, chase/extension riskini artırır. Ama event tanımı genişletilebilir (Çözüm C).

3. **STABIL BROKEN hard block kaldırılamaz.** Günlük destek tamamen çökmüşse yeni long açılmamalı.

4. **Causal safety asla gevşetilmemeli.** `available_at`, `confirmed_at`, `is_available_at()` kontrolleri dokunulmamalı.

5. **Structure owns direction kuralı dokunulmamalı.** Supporting domain'lar direction/thesis değiştiremez.

6. **Lifecycle state machine dokunulmamalı.** FLAT↔OPEN geçişleri, metadata dondurma, exit persistence.

---

## 10. Önerdiğin Değişiklik Sırası

### Faz 1: Gate Deduplication (En Yüksek Etki, En Düşük Risk)

| # | Değişiklik | Dosya | Etki |
|---|-----------|-------|------|
| 1.1 | `STRUCTURAL_TRANSITION_TO_RESOLVE` duplicate'ini kaldır | eligibility.py:119 | Move #12,#15,#19 |
| 1.2 | `STRUCTURAL_TRANSITION_TO_RESOLVE` duplicate'ini kaldır | scenario.py:259 | Move #12,#15,#19 |
| 1.3 | `TARGET_PATH_TO_RESOLVE` waiting'ini kaldır | scenario.py:252-253 | Move #4,#10,#12,#15,#16,#18 |
| 1.4 | `MORE_DIRECTIONAL_ROOM` duplicate'ini kaldır | scenario.py:246-248 | Move #4,#9,#12 |
| 1.5 | Counter-LT timing gate'ini kaldır | engine.py:322-323 | Move #4,#10,#16,#18 |
| 1.6 | Counter-LT opportunity gate'ini kaldır | engine.py:324-325 | Move #4,#10,#18 |

### Faz 2: Opportunity NONE Softening (Yüksek Etki, Orta Risk)

| # | Değişiklik | Dosya | Etki |
|---|-----------|-------|------|
| 2.1 | LIQUIDITY_MAGNET `hard_room_constraint=False` | opportunity.py:_target_semantics | Move #4,#9,#11,#12,#13,#17,#18,#19 |
| 2.2 | OPPORTUNITY_NONE soft blocker (WAITING, BLOCKED değil) | eligibility.py:100-101 | Yukarıdaki tüm hareketler |
| 2.3 | Target hysteresis mekanizması | opportunity.py:assess_opportunity | Move #1 oscillation |

### Faz 3: Execution Event Broadening (Orta Etki, Dikkatli Test)

| # | Değişiklik | Dosya | Etki |
|---|-----------|-------|------|
| 3.1 | Reaction CONFIRMED → synthetic execution event | execution_detect.py | Move #3,#5,#10 READY→BUY |

### Faz 4: Chase Guard (Düşük Öncelik, Safety)

| # | Değişiklik | Dosya | Etki |
|---|-----------|-------|------|
| 4.1 | EXTENDED timing state emission | timing.py:assess_timing | Geç giriş engeli |

---

## 11. Her Değişiklik İçin Yazılması Gereken Regression Testleri

### Faz 1 Testleri:

```python
# test_gate_dedup_no_duplicate_transition_waiting
# THESIS_TRANSITIONING durumunda eligibility ve scenario'dan
# STRUCTURAL_TRANSITION_TO_RESOLVE sadece permission katmanından gelmeli.
# Entry decision waiting list'inde en fazla 1 kez görünmeli.

# test_target_path_not_ready_does_not_block_qualified_scenario
# target_path.status=NO_OBSERVED_PATH iken, opportunity=AMPLE ve timing=READY ise
# scenario stage QUALIFIED olmalı (TARGET_PATH_TO_RESOLVE eklenmemeli).

# test_counter_lt_does_not_duplicate_timing_waiting
# Counter-LT + timing=FAILED durumunda:
# waiting list'inde NEW_SETUP_PATH olmalı ama
# COUNTER_LT_ST_REQUIRES_CONFIRMED_1H_SETUP OLMAMALI.

# test_counter_lt_does_not_duplicate_opportunity_waiting  
# Counter-LT + opportunity=COMPRESSED durumunda:
# MORE_DIRECTIONAL_ROOM olmalı ama
# COUNTER_LT_ST_REQUIRES_CLEAR_DIRECTIONAL_ROOM OLMAMALI.
```

### Faz 2 Testleri:

```python
# test_opportunity_none_is_soft_block_not_hard_block
# opportunity=NONE + hard_room_constraint=False durumunda:
# eligibility BLOCKED değil WAITING olmalı.
# Scenario stage BLOCKED değil DEVELOPING olmalı.

# test_liquidity_magnet_is_soft_room_constraint
# _target_semantics() LIQUIDITY_MAGNET için hard_room_constraint=False döndürmeli.
# SR ve liquidity_anchor olmayan cluster'lar hâlâ hard=True kalmalı.

# test_target_hysteresis_preserves_previous_target
# Önceki target hâlâ ahead ise ve room > none_max_atr ise,
# yeni bir nearer target seçilse bile önceki target korunmalı.

# test_opportunity_none_still_blocks_at_zero_room
# room ≤ 0.1 ATR (gerçek duvar) durumunda:
# hard_room_constraint=True kalmalı → BLOCKED.
```

### Faz 3 Testleri:

```python
# test_reaction_confirmed_produces_synthetic_execution_event
# reaction=CONFIRMED + timing=READY + structural=LONG/INTACT durumunda:
# detect_1h_execution_events() bir CONFIRMED event üretmeli.
# Bu event ExecutionEventKind.REACTION_CONFIRMATION olmalı.

# test_reaction_synthetic_event_not_produced_without_timing_ready
# reaction=CONFIRMED ama timing=DEVELOPING durumunda:
# synthetic event ÜRETİLMEMELİ.

# test_no_lookahead_in_reaction_execution_detection
# Reaction CONFIRMED event'i sadece bar close sonrası available olmalı.
# Open bar'da reaction CONFIRMED görünmemeli.
```

### Faz 4 Testleri:

```python
# test_extended_timing_emitted_after_large_move
# Move start'tan +15% ve 20+ bar sonra:
# timing state EXTENDED olmalı.
# eligibility EXTENDED durumunda WAITING veya BLOCKED olmalı.

# test_extended_not_emitted_for_small_moves
# +3% ve 5 bar sonra:
# timing EXTENDED OLMAMALI.

# test_chase_guard_does_not_block_early_entries
# +2% ve 3 bar sonra:
# timing READY kalabilmeli, EXTENDED tarafından ezilmemeli.
```

---

## Ek: Emin Olmadığım Noktalar

### 1 — Target Path'in Bağımsız Değeri Var mı?
Target path, opportunity'dan farklı olarak zone disposition (CLEARED/DEFENDED/PENDING) bilgisi taşıyor. Bu bilgi opportunity'da yok. Target path'i tamamen kaldırmak yerine, sadece waiting reason'ını kaldırmak yeterli olabilir. DEFENDED node'lar hâlâ scenario'da `ACTIVE_TARGET_PATH_NODE_DEFENDED` olarak görünüyor.

**Doğrulama:** Target path disposition'ın entry kararını gerçekten değiştirdiği vaka var mı?

### 2 — Opportunity Calibration Eşikleri Doğru mu?
`none_max_atr=0.739` çok dar olabilir. Bu ASELS'e özel bir kalibrasyon. Genel olarak 0.74 ATR mesafedeki bir liquidity seviyesi "hiç alan yok" demek mi, yoksa "az alan var" demek mi?

**Doğrulama:** Historical calibration verisi — 0.74 ATR içinde kalan target'ların ne kadarı gerçekten kâr tavanı oldu?

### 3 — Reaction CONFIRMED → Synthetic Event Causal Safety'yi Bozar mı?
Reaction assessment zaten causal (closed bar). Synthetic event sadece reaction CONFIRMED + timing READY birleştiğinde üretilirse, lookahead riski yok. Ama reaction CONFIRMED'in kendisi bazen sticky state'ten gelebilir — bu durumda "fresh" event olmadığı halde synthetic event üretilmiş olur.

**Doğrulama:** Reaction CONFIRMED state'inin sticky olup olmadığını kontrol et. `reaction.py` içinde confirmation_present hangi koşullarda True?

### 4 — Counter-LT Gate'leri Tamamen Kaldırılmalı mı?
Counter-LT durumunda timing ve opportunity gate'lerini kaldırmak, counter-LT trade'leri normal trade'lerle aynı standartta değerlendirir. Bu doğru olabilir (ST zaten bağımsız bir otorite), ama counter-LT trade'lerin risk profili farklı — LT SHORT iken ST LONG açmak daha riskli.

**Alternatif:** Counter-LT gate'lerini kaldırmak yerine, conflict assessment içine taşımak. Conflict zaten 3 family (reaction, participation, environment) değerlendiriyor. Counter-LT risk'i 4. family olarak eklenebilir.

### 5 — Move #1'de +13%'ten Sonra Target Kaybolması
Move #1'de +13%'ten sonra `TARGET id=-` ve `opportunity=UNKNOWN`. Bu targeting engine'ın artık upside target bulamadığı anlamına geliyor. Fiyat 429'a kadar çıkmış — tüm liquidity magnet'lar geride kalmış. Bu durumda UNKNOWN doğru mu, yoksa "alan açık" olarak mı yorumlanmalı?

**Doğrulama:** Targeting snapshot'ta upside cluster kalmadığında `NO_DIRECTIONAL_TARGET_OBSERVED_NOT_CLEAR_PATH` reason'u üretiliyor. Bu semantik olarak "hedef yok = yol açık" mı yoksa "hedef yok = bilinmiyor" mu?
