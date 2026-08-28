# Pozisyon Üretememe Analizi ve Karar-Esnekliği Planı

**Tarih:** 2026-08-28 · **Veri:** ASELS gerçek cache profili (1263 snapshot, 1263 barlık karar akışı)
· **Yöntem:** Kapı sayıları (ölçülmüş) + karar katmanı kod okuması (dosya:satır kanıtlı)
· **İlke:** Uydurma değil — her bulgu ya kodda yazılı bir kural ya da profilde ölçülmüş bir
sayı; tolerans önerileri piyasa yapısı ve olasılık matematiğiyle gerekçeli.

## 0. Ölçülmüş gerçek: sistem hiç pozisyon üretmiyor

1263 kararın tamamı WAIT (813) veya NO_TRADE (450); **BUY = 0**. Arbiter 777 kez seçim
yaptı ve **777'nin tamamı LONG_TERM**. LT seçildikten sonraki engel dağılımı:

| Kapı | Sayı (777 içinde) | engelleme oranı |
|---|---|---|
| CONTEXT_CONFLICT | 708 | **%91** |
| SETUP_TRIGGER (forming) | 443 | %57 |
| MORE_DIRECTIONAL_ROOM | 359 | %46 |
| MATERIAL_CONFLICT | 306 | %39 |

BUY için **hepsinin aynı barda** geçmesi gerekir (stage QUALIFIED ⟺ eligibility READY,
`scenario.py:272-275`). Bu tablo "şanssızlık" değil, tasarımın matematiksel sonucudur —
aşağıda beş ayrı kök neden.

---

## BULGU 1 — Arbiter: LT "var ama bloklu" olduğu anda ST'yi kalıcı olarak eziyor
*(kullanıcı teşhisi: "uzuna öncelik verdiği için kısa vadeye geçemiyor")*

**Kanıt (kod):** `decision/arbiter.py:30-34` — *"LONG_TERM has semantic priority. SHORT_TERM
is considered only after LONG_TERM is proven ABSENT; **UNKNOWN is not absence** and
therefore cannot trigger a fallback."* `decision/scenario.py:42-44` — *"a blocked LT
scenario still exists and therefore cannot be silently bypassed by an ST setup."*

**Kanıt (ölçüm):** LT presence PRESENT → 777/1263 (%%61). Bu 777'nin %91'i CONTEXT_CONFLICT
ile bloklu ama presence PRESENT kalıyor → ST hiç senaryo sahipliği alamıyor (ST seçim = 0).

**Piyasa gerçeği:** Trend + düzeltme rejiminde "LT tezi var ama girişi erken/bloklu" olması
NORMAL durumdur — ve tam da o düzeltme, ST reaksiyon işleminin oynanabilir olduğu andır
(trende düzeltmeden alım, klasik pullback-trade). Mevcut kural, ST'ye yalnızca "LT'nin
KANITLANMIŞ YOKLUĞUNDA" izin verir; trend yapan bir hissede bu şu demek: ST asla.
Epistemik olarak da uç: UNKNOWN (bilgi yok) bir **olumsuz kanıt** gibi davranılıyor.

## BULGU 2 — Tek bir "başarısız" bölge tüm ufku MATERIAL çatışmaya sokuyor (CONTEXT_CONFLICT %91)

**Kanıt (kod):** `decision/reaction.py:370` — FVG döngüsünde `failed_reaction OR invalid OR
full_fill` olan HER relevant bölge failure kaydı olur; `failure_present=True` →
`decision/conflict.py:48-58` — tek başına `ConflictSeverity.MATERIAL`; onaylanmış başka
bölge olsa bile (`REACTION_FAILED_WHILE_OTHER_REACTION_CONFIRMED`) yine MATERIAL →
eligibility WAIT "CONTEXT_CONFLICT".

**Kanıt (ölçüm + matematik):** Medyan **101 relevant bölge**; relevance penceresi yaş≤50 bar
+ mesafe≤5 ATR. Bölge başına canlı-hata olasılığı yalnızca **p=%2.4** olsa bile
P(≥1 hata) = 1−(1−0.024)¹⁰¹ ≈ **%91** — ölçülen %91'le birebir. Yani "çatışma" değil,
büyük bir kümede *kaçınılmaz en-az-bir-hata*: kapı fiilen hep açık.

**Piyasa gerçeği:** `full_fill` = FVG boşluğunun tamamen dolması = bir bölgenin **normal
yaşam döngüsü tamamlanması** (gap-fill eğilimi borsada iyi bilinir; boşlukların çoğu
sürdürme gelmezse dolar). Bunu "yönsel başarısızlık" saymak, tamamlanmayı çelişki okumak.
Gerçek kenar: birincil bölge hayatta mı + risk/ödül asimetrisi — "101 bölgenin hiçbirinde
50 bar içinde hiçbir hata yok" talebi tam olarak *kusursuz işlem* arayışı.

## BULGU 3 — BREAKOUT_ABSORPTION: heavy-conflict'in %100'ü, tek ve yapışkan bir disjunct

**Kanıt (kod):** `engines/volume_participation_final.py:166-170` — destekli/korumalı kırılım +
üst/alt absorpsiyon CONFIRMED → heavy conflict. Absorpsiyon bir kez CONFIRMED olunca stage
kolay kolay geri almaz → disjunct yapışkan.

**Kanıt (ölçüm):** 987 heavy-conflict vakasının **987'sinde** (%100) aktif disjunct
BREAKOUT_ABSORPTION; referans yaş medyanı **246 bar**. (D2'nin 24-bar stale düşürmesi karar
katmanında var; koşu istatistiği 325 koşu/662 bar → ortalama koşu ~2 bar, yani stale eşiği
nadiren yetişiyor, disjunct sürekli yeniden tutuşuyor.)

**Piyasa gerçeği:** Kırılım sonrası geri çekilip hacimli yutulma (retest/absorption) sağlıklı
kırılımın **normal parçasıdır** (Wyckoff "backing and filling"). Kırılımı öldüren şey hedefli
değil köke dönen kapanıştır. "Kırılım + absorpsiyon CONFIRMED = ağır çatışma" eşlemesi retesti
yanlış okur; 246 barlık absorpsiyon hiçbir şey anlatmaz.

## BULGU 4 — MORE_DIRECTIONAL_ROOM: kendi dağılımıyla self-referans, düzeltmede room zaten düşüktür

**Kanıt (kod):** `decision/opportunity.py:125-128` — room ≤ compressed_max_atr → COMPRESSED →
`decision/eligibility.py:128` WAIT "MORE_DIRECTIONAL_ROOM". Sınırlar hissenin kendi room
dağılımından kalibre ediliyor (`decision/calibration.py`).

**Kanıt (ölçüm + matematik):** Medyan room LT 1.476 / ST 1.537 ATR ve %46 bloklanıyor →
sınırlar dağılımın içine düşüyor. Yüzdelik-tabanlı kalibrasyon self-referanstır: dağılım ne
olursa olsun alt kuyruk HER ZAMAN "compressed" kalır. Ayrıca düzeltme-tepesi girişinde
fiyattan hedefe mesafenin düşük olması, fiyATIN BÖLGEYE YAKLAŞTIĞINI gösterir — bu giriş
anıdır, engel değil: **düzeltmede iskonto, room'un kendisidir.** (1.5 ATR hedefe karşı
~0.8-1.0 ATR stop tipik BIST senaryosunda RR ≈ 1.5-1.9; matematiksel olarak reddedilecek
bir işlem değil.)

## BULGU 5 — Sentez: "aynı barda mükemmellik" çarpımı → BUY=0 tasarımın sonucu

BUY ⟙ arbiter seçimi ∧ hiçbir WAIT maddesi yokluğu (conflict + room + trigger + coverage +
permission hepsi aynı anda). Ölçülen koşullu engel oranlarıyla üst-sınır hesabı:
P(geçiş) ≲ 0.09 × 0.43 × 0.54 ≈ **%2** — coverage/permission/execution henüz saymadan.
1263 snapshot'ta 0 BUY, bu çarpımın gözle görülen sonucu. Üstelik kapılar aynı bölge
kümesinden beslendiği için korelasyon olumlu yönde değil (aynı bölge hem failure hem room
hem trigger'ı besliyor): düzeltme anında üç kapı da aynı anda kapanıyor.

> **Tez:** Sistem trend-devam *kusursuzluğu* arıyor. Piyasanın iş yaptığı yer düzeltme
> adımlarıdır — ve yukarıdaki üç kapı (1, 2, 4) tam da düzeltme durumunu cezalandırıyor.

---

## Tolerans Paketi — DURUM: **T1+T2+T4 UYGULANDI** (2026-08-28, kullanıcı onaylı; T3/T5 beklemede)

> Amaç sinyali yapay artırmak değil; *istatistiksel olarak kaçınılmaz* olanı veto eden
> kuralları, kalan sert kapıları koruyarak gevşetmek. Kusursuz işlem yoktur; iyi işlem
> = kabul edilebilir hata oranı + pozitif asimetri.

### T1 — Hazır-ST sahiplik devri (arbiter) — *Bulgu 1*
LT PRESENT ama stage `BLOCKED`/`DEVELOPING` iken ST `QUALIFIED` ise → ST sahiplik alır
(`SHORT_TERM_FALLBACK_WHILE_LONG_TERM_BLOCKED`); LT `UNKNOWN` iken ST QUALIFIED ise → ST
(`..._WHILE_LONG_TERM_UNRESOLVED`). LT PRESENT+QUALIFIED → LT önceliği **aynen korunur**.
Skor karşılaştırması eklenmez (tasarım ilkesi bozulmaz); sadece "hazır vs bloklu" ayrımı.
*Sert kapı korunur:* ST yine kendi tüm kapılarından geçmek zorunda.

### T2 — Reaksiyon çatışmasını oran/ağırlık bazlı yap — *Bulgu 2*
(a) `full_fill`/`invalid` (yaşam-döngüsü tamamlanması) failure sayılmasın: yalnız canlı
`failed_reaction` oy versin (tamamlanan bölge nötr). (b) Onay canlıyken (confirmation_present)
failure yalnızca **birincil bölgedeyse** (en yakın/yüksek kaliteli) MATERIAL; ikincil/uzak
failure → LOW. *Sert kapı korunur:* birincil bölgenin gerçek başarısızlığı hâlâ MATERIAL.

### T3 — BREAKOUT_ABSORPTION canlılık koşulu — *Bulgu 3 (D2 adım 3'ün somutu)*
Disjunct yalnızca absorpsiyon CONFIRMED sonrası **24 bar içinde** ve fiyat hâlâ absorpsiyon
referans bölgesinde/kırılım kökünde iken heavy olsun; yoksa düşsün. (24 = mevcut stale eşiğiyle
tutarlı.) **Not:** `engines/` parmak izinde → rebuild tetikler; ama zaten sınırlı-projeksiyon
için yapılacak tek rebuild'e biner → **ek maliyet 0**.

### T4 — Birincil bölgede room istisnası — *Bulgu 4*
Fiyat onaylanmış/kaliteli bir birincil bölgenin içinde veya hemen komşusunda iken
COMPRESSED → WAIT yerine MODERATE muamelesi (`AT_PRIMARY_ZONE_DISCOUNT`). `NONE` (hedef
yok) **sert kalır**. *Sert kapı korunur:* hedefi olmayan giriş yine yok.

### T5 — (Koşullu, ayrı onay) Kademeli hazırlık: ARMED durumu — *Bulgu 5*
T1-T4 sonrası profil yeniden ölçülür; BUY hâlâ ~0 ise: WATCH → ARMED (conflict LOW + room
OK + trigger FORMING) → READY zinciri; ARMED'dayken bölge-doku + tetik tek koşulla
konsolide ateşlenir. Aynı kapılar korunur, "aynı bar mükemmelliği" kalkar. Daha büyük
mimari adım — ilk dördün ölçümüne kadar bekletilir.

### Cache etkisi (önemli)
T1, T2, T4 (ve T5) **karar-değerlendirme katmanında** — `persistent_state.py:110-113`
açıkça dışarıda bırakmış → **cache bozulmaz, rebuild yok**. T3 engines/ içinde → tek
rebuild gerektirir (sınırlı-projeksiyon rebuild'i ile birleşir).

### Doğrulama planı
1. Her adımdan sonra tam test paketi (mevcut 1171) + yeni birim testleri (eski SERT
   davranışın korunan kısımları: birincil-bölge failure, room NONE, LT önceliği).
2. Kullanıcı makinesinde profil (cache HIT): metrikler — ARBITER dağılımı (ST>0 beklentisi),
   CONTEXT_CONFLICT (708 → belirgin düşüş), ENTRY ACTION (BUY>0 beklentisi), NO_TRADE nedenleri.
3. Kabul ölçütü: sinyaller artar ama WAIT/NO_TRADE hâlâ çoğunluktur — gevşetme değil,
   mükemmellik-şartının kaldırılması.

### Uygulama sırası
T1 + T2 + T4 (tek commit, cache-güvenli) → ölçüm → T3 (rebuild'e biner) → ölçüm → T5 kararı.

## Uygulama Notları (T1+T2+T4)

- **T1** `decision/arbiter.py`: `st_qualified` (PRESENT + QUALIFIED) koşuluyla üç yeni dal
  (LT BLOCKED/DEVELOPING → ST; LT UNKNOWN → ST). LT QUALIFIED önceliği ve skor-karşılaştırma
  yasağı aynen korundu. Yeni reason'lar: `SHORT_TERM_FALLBACK_WHILE_LONG_TERM_BLOCKED`,
  `SHORT_TERM_FALLBACK_WHILE_LONG_TERM_UNRESOLVED`.
- **T2a** `decision/reaction.py`: FVG döngüsünde yalnız canlı `failed_reaction` failure oyu;
  `invalid`/`full_fill` → `FVG_LIFECYCLE_COMPLETED` (nötr, developing de işaretlemiyor).
  OB failure-mode oyları (GAP_THROUGH vb.) değişmedi; superseded mekanizması değişmedi.
- **T2b** `decision/conflict.py`: `failure_present ∧ confirmation_present` →
  `REACTION_FAILED_SECONDARY_LINEAGE`/LOW ("SECONDARY_LINEAGE"); failure tek başına →
  MATERIAL (sert kapı aynen). *Birincil-bölge* kavramı, mevcut superseded-filtresi
  (onaylı bölgeyle çakışan failure zaten atılıyor) + onay-kapısıyla gerçeklendi;
  fiyat-mesafesi conflict katmanına taşınmadı.
- **T4** `decision/eligibility.py` (+`engine.py` çağrısı): COMPRESSED + reaction
  `confirmation_present` → `ROOM_COMPRESSED_AT_PRIMARY_ZONE_DISCOUNT` reason, WAIT yok;
  confirmation yok / reaction verilmedi → eski `MORE_DIRECTIONAL_ROOM` WAIT aynen.
  Tur4 sözleşme testi bilinçli güncellendi (yüzey tipli kaldı).
- Testler: `tests/test_decision_tolerance_package.py` (14 yeni) + 5 eski sözleşme testi
  yeni davranışa göre yeniden yazıldı (korunan kısımlar için karşıt vakalar eklendi).
  Tam paket: **1188 passed / 4 skipped**.
- Cache: dokunan dosyaların tamamı (`arbiter`, `reaction`, `conflict`, `eligibility`,
  `engine`) parmak-izi kümelerinin DIŞINDA → mevcut cache HIT kalır, rebuild yok.

---

## BÖLÜM 12 — Ölçülmüş Sonuçlar ve Yeni Kök Neden: Çift Conflict Değerlendirmesi (2026-08-28)

> Kullanıcı makinesinde rebuild + profil (T1+T2+T4 koduyla). Tüm sayılar ölçümdür.

### 12.1 Performans (soğuk rebuild)

| Metrik | Önce | Sonra | Değişim |
|---|---|---|---|
| BUILD_SECONDS | 685.712 | **421.132** | **−%38.5** |
| FROZEN_CACHE_FILE_MB | 491.675 | 488.872 | −%0.6 |
| REASON_PROFILE_SECONDS | 43.8 | 38.4 | −%12 |
| VERIFY (sidecar digest) | — | 0.002 s / load 0.004 s | anlık |
| CHECKPOINTS_SEEDED | 7 | 9 | sonraki refresh artımlı |

**Engine replay toplamı sadece ~44.9 s (build'in %10.7'si)** — darboğaz replay değil,
cutoff başına bağlam kompozisyonu/dondurması (teşhis doğrulandı). En pahalı motorlar:
30m pattern 7.42 s, 30m liquidity 6.67 s, 30m market_structure 5.20 s. Cache boyutu
neredeyse değişmedi (−2.8 MB): sınırlı projeksiyon CPU/nesne maliyetini kesti ama
pickle ayak izini değil; ~87 s'lik HIT pickle yükü için trimming hâlâ ertelenmiş iş.

### 12.2 Tolerans paketi doğrulaması

- **T2 çalıştı:** LT REACTION MATERIAL 606 → **229**; yeni `REACTION_FAILED_SECONDARY_LINEAGE`
  LOW 377; MATERIAL_CONFLICT_TO_RESOLVE 306 → **193**.
- **T4 çalıştı:** LT MORE_DIRECTIONAL_ROOM 359 → **120** (239 kez birincil-bölge iskontosu).
- **T1 henüz ateşlenmedi — beklenendir:** ST stage QUALIFIED = 0 (DEVELOPING 549). ST kendi
  kapılarına takılıyor: SETUP_TRIGGER 403, CONTEXT_CONFLICT 300, ROOM 257. Zincir bir alt
  seviyeye indi; ARBITER hâlâ 777 LONG_TERM / 0 SHORT_TERM.

### 12.3 YENİ KÖK NEDEN: iki ayrı conflict değerlendirmesi çelişiyor

**Ölçüm:** Karar katmanı conflict tablosu HIGH'ı yalnızca **11**_snapshotta görüyor;
buna karşılık permission zarfı **766** kez `CONTEXT_CONFLICT_HIGH` ile BLOCKED →
eligibility `CONTEXT_CONFLICT_TO_RECONCILE` = WAIT'lerin **%88'i** (766/871).

**Kod (kanıt):** permission `context/permissions.py:101` `axes.conflict is HIGH` → BLOCKED.
`axes.conflict` `context/axes.py:evaluate_conflict`'ten gelir ve `continuation is
CONFLICTING` → **HIGH** yapar (`axes.py:566`). `CONFLICTING` ise `evaluate_continuation`
'da (`axes.py:291`): LT anchor'ındaki **en son yapısal olayın yönü tezin tersiyse** —
**olay tipi CHOCH bile olsa** — döner.

**Piyasa karşılığı:** Trend tezi LONG iken düzeltme, anchor TF'de counter-CHOCH üretir;
bu düzeltmenin doğal yapısal izidir. Kod bunu HIGH conflict sayar → kapı kilitlenir;
kapı yalnızca tez yönünde yeni BOS geldiğinde açılır — o ana kadar bölge ve iskonto
kaçmıştır. Karar katmanasının nuanslı tablosu aynı anı LOW/NONE okur (HIGH 11: gerçek
reversal'lar). Kullanıcının "düzeltmeler de önemli" gözlemi, birebir bu kod satırıdır.

### 12.4 Öneri T6 — counter-CHOCH severity düzeltmesi (onaya bağlı)

`context/axes.py`: karşı yönlü son olay **BOS** ise HIGH **kalır** (yapısal süreklilik
gerçekten kırılmıştır); karşı yönlü son olay **CHOCH** ise HIGH → **MATERIAL** (düzeltme
izidir; bağımsız-aile kapısı zaten MATERIAL'ı yönetir: WAIT ama hard değil).
Yönlü semantiği zayıflatmaz — karşı BOS veto yetkisini korur.

**Maliyet:** `context/` parmak-izi kümesinde → **bir rebuild daha (~7 dk)**. T3
(engines/) istenirse aynı rebuild'e biner → ek maliyet 0. CACHENOT: karar katmanı
dosyalarında değişiklik yok.
