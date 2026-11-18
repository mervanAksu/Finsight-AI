---
name: ui-design-system
description: FinSight AI web arayüzünün görsel dili - renk değişkenleri, tipografi, boşluklar, bileşen kalıpları, grafik kuralları ve erişilebilirlik. Arayüzde yeni ekran veya bileşen yazılırken okunur.
---

# FinSight AI tasarım sistemi

## Kişilik
Sakin, güvenilir, veriye odaklı bir finans aracı. Süs yerine okunabilirlik; her sayı
kaynağıyla birlikte. Gösterişli gradyan, gölge yığını ve emoji yok.

## Renkler (CSS değişkenleri, `:root` ve `[data-theme="dark"]`)
| Değişken | Açık | Koyu | Kullanım |
| --- | --- | --- | --- |
| --bg | #f6f7f9 | #0d1117 | sayfa zemini |
| --surface | #ffffff | #161b22 | kartlar |
| --surface-2 | #f0f2f5 | #1f2630 | ikincil zemin, tablo başlığı |
| --border | #e3e6eb | #2a313c | ayırıcılar |
| --text | #14181f | #e6e9ee | ana metin |
| --text-2 | #5b6573 | #9aa4b2 | ikincil metin |
| --accent | #2456d6 | #6b95ff | vurgu, bağlantı, seçili sekme |
| --positive | #13865b | #3fc58a | olumlu değişim, olumlu ton |
| --negative | #c2410c | #ff8a5c | olumsuz değişim, olumsuz ton |
| --warning | #b7791f | #e3b341 | uyarı, eksik veri |
| --chart-1…5 | #2456d6, #13865b, #8b5cf6, #c2410c, #0891b2 | açık tonları | seriler |

Renk tek başına anlam taşımaz: artış/azalış ok işareti ve işaretli sayıyla da gösterilir.

## Tipografi ve boşluk
- Yazı tipi: sistem sans-serif yığını (`Inter, "Segoe UI", system-ui, sans-serif`); sayılar
  için `font-variant-numeric: tabular-nums`.
- Ölçek: 12 / 13 / 14 (gövde) / 16 / 20 / 28 / 36 px.
- Boşluk ölçeği 4'ün katları; kart iç boşluğu 20–24 px; köşe yarıçapı 12 px (kart), 8 px (düğme).

## Düzen
- Üstte sabit başlık: logo, şirket arama kutusu, tema düğmesi, dil seçimi.
- Şirket sayfası: başlık şeridi (ad, ticker, CIK, sektör, skor rozeti) + sekmeler:
  Özet · Finansallar · Risk ve Ton · Soru-Cevap · Raporlar.
- İçerik genişliği en fazla 1280 px; 12 sütunlu ızgara; 900 px altında tek sütun.
- Sayfanın altında her zaman "Yatırım tavsiyesi değildir" uyarısı.

## Bileşenler
- **Metrik kartı:** etiket (13 px, --text-2), büyük değer (28 px), YoY rozeti (↑/↓ ve renk),
  altında dönem ve kaynak bağlantısı.
- **Skor göstergesi:** yarım daire veya halka; ortada toplam puan ve bant adı; altında bileşen
  çubukları (ağırlık, puan, açıklama).
- **Tablo:** sayılar sağa hizalı; eksik değer "—" ve tooltip'te neden.
- **Kaynak etiketi:** küçük gri çip "10-K · 2025-06-30 · 0000950170-25-…" ve EDGAR bağlantısı.
- **Boş/yükleniyor/hata durumları:** iskelet (skeleton) çubuklar; hata kartında yeniden dene.

## Grafikler (d3 ölçekleri + SVG)
- Zaman serisi: çizgi + noktalar veya gruplu çubuk; eksen etiketleri dönem bitiş tarihi.
- Izgara çizgileri ince ve soluk; eksen çizgisi yok denecek kadar hafif.
- Fareyle üzerine gelince değer, dönem ve kaynak gösteren tooltip.
- Büyük sayılar kısaltılır: 1,2 mn $ · 3,4 mr $ (Türkçe biçim), tooltip'te tam değer.
- Grafik başlığı bulguyu söyler ("Gelir 3 yılda %18 arttı"), konuyu değil.
