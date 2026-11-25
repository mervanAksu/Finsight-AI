---
name: ui-design-system
description: FinSight AI web arayüzünün görsel dili ("Dipnotlu tablo") - renk tokenları, tipografi, boşluklar, bileşen kalıpları, grafik kuralları, TTM/tazelik gösterimi ve erişilebilirlik. Arayüzde yeni ekran veya bileşen yazılırken okunur.
---

# FinSight AI tasarım sistemi: "Dipnotlu tablo" (Aşama 4)

## Yön
Ürünün konusu SEC başvurusudur: mali tablo, dipnot, form numarası, accession. Arayüz bu
dünyanın dilini kullanır: **her sayı bir dipnota bağlıdır**. Kaynağı olan her rakamın
altında noktalı çizgi vardır. Üzerine gelince ya da Sekme ile odaklanınca fosforlu kalem
vurgusu (`--mark`) çıkar ve ipucu form, accession, dönem, XBRL etiketi ve TTM parçalarını
gösterir. Cesaret yalnızca bu etkileşimde ve skor cetvelindedir; geri kalan her şey sakin
bir finans yayını disiplinindedir: ince çizgiler, tabular rakamlar, kısıtlı ve anlamlı renk.

Kaçınılanlar: her şeyi aynı yuvarlak karta koymak, mor-mavi gradyan, cam efekti, emoji,
gelişigüzel gölge, "hero + 3 özellik kartı" düzeni, büyük harfli üst etiketler (eyebrow),
yarım daire gösterge, renkli hap (pill) rozet yığınları.

## Renk tokenları (`src/styles.css`, `:root` ve `[data-theme="dark"]`)
| Token | Açık | Koyu | Kullanım |
| --- | --- | --- | --- |
| --paper | #f5f6f3 | #0f1418 | sayfa zemini (soğuk kâğıt; krem değil) |
| --surface | #ffffff | #151b20 | yaprak (birincil veri bölümü) |
| --surface-2 | #eef0ec | #1d252b | iz çubukları, satır üzeri |
| --latest-bg | #eef4f5 | #16252b | TTM / en son dönem sütunu, seçili satır |
| --rule | #d8dcd5 | #2b353d | satır ayırıcı, ince çizgi |
| --rule-ctl | #828b93 | #6b7782 | denetim kenarlığı (≥ 3:1) |
| --rule-strong | #1a1f24 | #c9d1d7 | tablo başlığı alt çizgisi, kenar bölüm üst çizgisi, toplam çift çizgisi |
| --ink / --ink-2 / --ink-3 | #161a1f / #48515b / #5f6872 | #e6eaed / #aab4bc / #939ea8 | metin kademeleri (ink-3 bile ≥ 5:1) |
| --accent | #0b5a73 (petrol) | #6cc0d9 | bağlantı, etkin sekme, birincil düğme, TTM etiketi, skor anatomisi |
| --mark | #fbe38e | #5c4d12 (+ --mark-ink #fff2bf) | kaynak vurgusu (fosforlu kalem), "en iyi" işareti |
| --positive | #17784a | #4cc38a | olumlu değişim, olumlu ton, "Güçlü" bandı |
| --negative | #b3261e | #ff8a7a | olumsuz değişim, eklenen risk, "Zayıf" bandı |
| --warning | #8a5a00 | #e7b84a | eksik veri, "İzlenmeli", tazelik bandı |
| --chart-1…4 | #0b5a73, #7fa9b6, #a1661b, #5e5a99 | #6cc0d9, #3f7484, #d9a04a, #a49fe0 | seriler (petrol, açık petrol, okr, arduvaz mor) |

Kurallar: renk tek başına anlam taşımaz (ok işareti, işaretli sayı, etiket metni de var).
Yeşil ve kırmızı yalnızca yön veya ton anlamı için kullanılır; vurgu rengi petroldür. Skor
bantları: Zayıf negative, İzlenmeli warning, Dengeli accent, Güçlü positive.

## Tipografi (yerel dosyalar, SIL OFL 1.1: `frontend/src/fonts/`, lisanslar yanında)
- **Bitter** (değişken, 400–700, slab serif): sayfa ve bölüm başlıkları, şirket adı, büyük
  rakamlar (skor 54 px, toplam, risk sayıları, ton değerleri).
- **Fira Sans** (400/400i/500/600): arayüz, gövde, tablolar. Rakamlar her yerde
  `font-variant-numeric: tabular-nums lining-nums`. İngilizce alıntılar (rapor cümleleri)
  Fira Sans italik.
- Ölçek (px): 11 · 12.5 · 13.5 · 15 (gövde) · 17 · 19–20 (bölüm başlığı) · 24 · 27–36 (sayfa) ·
  44 (ana sayfa başlığı) · 54 (skor). Büyük harfli etiket yok; tablo başlıkları 12.5 px, orta ağırlık.
- Satır uzunluğu ≤ 72ch (açıklama, cevap metni).

## Boşluk, ızgara, köşe
- 4'ün katları; bölüm arası (gutter) 24 px (≤ 720 px'te 16).
- İçerik en fazla 1320 px; 12 sütun. ≤ 1100 px: 7/5/8'lik bölümler tam genişlik, 4'lükler
  ikişer (`span-md-12` ile tam). ≤ 720 px: tek sütun, başlıkta arama ikinci satıra iner.
- Köşe: denetimler 3 px, yapraklar 4 px, damgalar 2 px. Gölge yalnızca açılır katmanlarda
  (`--shadow-pop`: ipucu, arama listesi).

## Bölüm türleri (hiyerarşi kapla değil, kapsayıcı türüyle)
- **Yaprak** (`Panel`, varsayılan `variant="sheet"`): birincil veri: skor bileşenleri,
  kalemler, grafikler, tablolar. Beyaz zemin, 1 px çizgi.
- **Kenar bölümü** (`variant="margin"`): ikincil bilgi: ton özeti, risk sayıları, son
  başvurular. Kutusuz; üstte 2 px `--rule-strong` çizgi.
- Tablo toplamı: üstte 3 px **çift çizgi** (muhasebe toplamı). Toplam satırları (net kâr,
  özkaynak) 1 px koyu çizgi + kalın.

## Bileşenler
- **Şirket künyesi** (`Masthead`): ticker damgası, borsa, ad (Bitter 36), olgular satırı
  (CIK, sektör, mali yıl sonu, son analiz, **SEC durumu**). Sağda skor bloğu: büyük rakam,
  "/ 100", bant adı, **skor cetveli** (0–100 doğrusal, 45/60/75 eşikleri, bant bölütleri,
  dikey işaret) ve **dayanak satırı** `TTM · 30 Eyl 2026 · 10-Q` (ipucunda başvuru,
  accession, veri tarihi, yöntem sürümü, TTM tanımı). Eski sunucuda yalnızca "Dönem …".
- **Tazelik** (`components/Freshness.tsx`, `/freshness`): `stale: false` → "SEC ile güncel";
  `stale: true` → künyenin altında okr zeminli bant "Yeni rapor yayımlandı: 10-Q (28 Eki
  2026)" + **Güncelle** (POST /ingest, ilerleme bandın içinde); `stale: null` → "SEC kontrol
  edilemedi" + neden ipucu + "Yeniden kontrol et"; 404 (eski sunucu) → hiçbir şey.
- **Kaynaklı sayı** (`Figure`): noktalı alt çizgi; hover/odak/açık iken `--mark` zemin.
  İpucu (`SourceDetails`): tam değer, form (+TTM), accession, yayım tarihi, dönem, XBRL,
  TTM parçaları (yıllık, bu yılın başından bugüne, geçen yılın aynı dönemi).
- **Dönem tabanı etiketi** (`BasisTag`): çerçeveli küçük damga: `TTM` (petrol), `Yıllık`,
  `Bilanço` (anlık kalemler; bilanço kalemine asla "TTM" yazılmaz).
- **Form damgası** (`FormBadge`): 10-K petrol, 10-Q arduvaz, 8-K gri; çerçeveli, dolgusuz.
- **Skor bileşenleri tablosu**: bileşen (açılır: göstergeler + açıklama + not/kaynak),
  ağırlık, puan çubuğu (petrol) + tonlu puan, **katkı** (ağırlık × puan / kullanılan ağırlık),
  çift çizgili toplam. Üstte **anatomi şeridi**: her bileşen ağırlığı kadar geniş, dolu kısmı
  kazanılan puan. Altında `basis_notes`, ağırlık ve yöntem sürümü.
- **Temel kalemler**: tablo (kalem + taban etiketi + tarih | değer | yıllık değişim).
  `key_metrics[].ttm` varsa o gösterilir; yoksa yıllık değer ve `ttm_reason`.
- **Değişim** (`DeltaBadge`): ok + işaretli yüzde, renk yönün olumlu olup olmadığına göre; hap yok.
- **Oranlar tablosu**: ad + yön oku + formül satırı; TTM sütunu `--latest-bg` ve `TTM`
  damgası (`period_info` varsa ondan); eğilim çizgisi; son değişim (puan/kat).
- **Soru-Cevap**: sohbet balonu yok, belge düzeni. "Soru" (Bitter 20) → "Cevap" (16 px,
  1.68 satır) → numaralı **dipnot** kaynak listesi (üstte koyu çizgi). Atıf `[n]`
  tıklanabilir küçük damga; ilgili dipnotu fosforla vurgular.
- **Durumlar**: iskelet çubuklar (soluklaşma; reduced-motion'da durur), hata kartı (sol
  kırmızı çizgi + Yeniden dene), boş durum yönlendirici cümle + eylem.
- **Uyarı (NFR-12)**: her analiz ekranının sonunda `Disclaimer` + alt bilgide tekrar.

## Grafikler (d3 ölçekleri + elle SVG)
- Düz çubuk (1 px köşe), ince ızgara (`--rule`), sıfır çizgisi `--rule-strong`.
- En son dönem (TTM / son çeyrek bilançosu) aynı seri renginde **taralı** çubuk; eksen
  etiketi petrol ve "TTM Eyl 2026". Yıllık seri ile karışmaz.
- Başlık bulguyu söyler ("Gelir 2 yılda %31,7 arttı").
- Üzerine gelince / odakta ipucu: dönem, seriler, YoY, kaynak (form, tarih, accession).
- Büyük sayılar kısaltılır (268 mr $; en: $268B), tam değer ipucunda.

## Etkileşim ve hareket
- Hareket yalnızca kullanıcı eylemine yanıt: vurgu geçişi 150 ms, açılır satır oku 180 ms,
  ilerleme çubuğu. Otomatik giriş animasyonu yok. `prefers-reduced-motion` tümünü kapatır.
- Odak halkası: 2 px `--accent`, 2 px boşluk; tüm düğme, bağlantı, sekme, grafik dönemi,
  kaynaklı sayı klavyeyle erişilebilir. İpucu Escape ile kapanır.

## Erişilebilirlik kontrol listesi
- Metin kontrastı ≥ 4.5:1 (token çiftleri hesaplandı; en düşük açıkta pos/pos-soft 4.72),
  denetim kenarlığı ≥ 3:1.
- Grafiklerde `role="img"` + `aria-label`; tablo başlıkları `scope`; açılır satırlarda
  `aria-expanded`/`aria-controls`; sekmelerde `aria-current="page"`.
- 390 / 820 / 1440 px'te sayfa düzeyinde yatay kaydırma yok (geniş tablolar kendi
  kapsayıcılarında kayar; `.table-wrap` `position: relative`).
