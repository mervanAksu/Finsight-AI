"""Sahte (demo) API verisini üretir: frontend/mock/*.json.

Şirketlerin tamamı KURGUSALDIR (EXMP, NVLT, HRBR, QNTA ...). CIK numaraları SEC'te
kullanılmayan 999xxxx aralığından seçilmiştir. Oranlar ve skor,
.claude/skills/financial-analysis/SKILL.md kurallarıyla bu betikte hesaplanır; böylece
sahte veri kendi içinde tutarlıdır (oran = kalemlerden, skor = oranlardan).

Kullanım:  python3 frontend/scripts/gen_mock_data.py
Yalnızca standart kütüphane kullanır.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "mock"
B = 1_000_000_000

DISCLAIMER = (
    "Bu analiz yalnızca bilgilendirme amaçlıdır ve yatırım tavsiyesi değildir. "
    "Veriler SEC EDGAR başvurularından otomatik çıkarılmıştır; hata içerebilir."
)

METRIC_LABELS = {
    "revenue": "Gelir",
    "cost_of_revenue": "Satışların maliyeti",
    "gross_profit": "Brüt kâr",
    "operating_income": "Faaliyet kârı",
    "net_income": "Net kâr",
    "operating_cash_flow": "İşletme nakit akışı",
    "capex": "Yatırım harcamaları",
    "total_assets": "Toplam varlıklar",
    "current_assets": "Dönen varlıklar",
    "current_liabilities": "Kısa vadeli yükümlülükler",
    "total_liabilities": "Toplam yükümlülükler",
    "stockholders_equity": "Özkaynak",
}
DURATION = {"revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
            "operating_cash_flow", "capex"}
TAGS = {
    "revenue": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "cost_of_revenue": "us-gaap:CostOfGoodsAndServicesSold",
    "gross_profit": "us-gaap:GrossProfit",
    "operating_income": "us-gaap:OperatingIncomeLoss",
    "net_income": "us-gaap:NetIncomeLoss",
    "operating_cash_flow": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
    "capex": "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
    "total_assets": "us-gaap:Assets",
    "current_assets": "us-gaap:AssetsCurrent",
    "current_liabilities": "us-gaap:LiabilitiesCurrent",
    "total_liabilities": "us-gaap:Liabilities",
    "stockholders_equity": "us-gaap:StockholdersEquity",
}
DERIVED = {
    "gross_profit": ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                     "us-gaap:CostOfGoodsAndServicesSold"],
    "total_liabilities": ["us-gaap:LiabilitiesAndStockholdersEquity", "us-gaap:StockholdersEquity"],
}

RATIOS = [
    ("gross_margin", "Brüt kâr marjı", "Brüt kâr / Gelir", "percent", True),
    ("operating_margin", "Faaliyet kâr marjı", "Faaliyet kârı / Gelir", "percent", True),
    ("net_margin", "Net kâr marjı", "Net kâr / Gelir", "percent", True),
    ("roa", "Aktif kârlılığı (ROA)", "Net kâr / Ortalama toplam varlıklar", "percent", True),
    ("roe", "Özkaynak kârlılığı (ROE)", "Net kâr / Ortalama özkaynak", "percent", True),
    ("current_ratio", "Cari oran", "Dönen varlıklar / Kısa vadeli yükümlülükler", "times", True),
    ("debt_to_equity", "Borç / özkaynak", "Toplam yükümlülükler / Özkaynak", "times", False),
    ("liabilities_to_assets", "Yükümlülük / varlık", "Toplam yükümlülükler / Toplam varlıklar",
     "percent", False),
    ("ocf_to_net_income", "Nakit akışı kalitesi", "İşletme nakit akışı / Net kâr", "times", True),
    ("fcf_margin", "Serbest nakit akışı marjı", "(İşletme nakit akışı − Yatırım harcamaları) / Gelir",
     "percent", True),
]

ITEM_LABELS = {
    "1.01": "Önemli kesin anlaşma yapılması",
    "2.02": "Faaliyet sonuçları ve finansal durum",
    "5.02": "Yönetici veya direktör ayrılığı ya da ataması",
    "5.07": "Hissedar oylamasına sunulan konular",
    "7.01": "Regulation FD açıklaması",
    "8.01": "Diğer olaylar",
    "9.01": "Finansal tablolar ve ekler",
}


# ---------------------------------------------------------------- yardımcılar
def tr_num(x: float, digits: int = 1) -> str:
    s = f"{x:,.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def tr_pct(x: float, digits: int = 1) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}%{tr_num(abs(x) * 100, digits)}"


def lin(x: float, a: float, b: float) -> float:
    if a < b:
        if x <= a:
            return 0.0
        if x >= b:
            return 100.0
        return (x - a) / (b - a) * 100
    if x >= a:
        return 0.0
    if x <= b:
        return 100.0
    return (a - x) / (a - b) * 100


def r1(x: float) -> float:
    return round(x, 1)


def band(total: float) -> str:
    if total >= 75:
        return "Güçlü"
    if total >= 60:
        return "Dengeli"
    if total >= 45:
        return "İzlenmeli"
    return "Zayıf"


def accession(cik: str, year: int, seq: int) -> str:
    return f"{cik}-{str(year)[2:]}-{seq:06d}"


def filing_url(cik: str, acc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{acc}-index.htm"


def folder_url(cik: str, acc: str) -> str:
    """Source.url (sözleşme v1.1): başvuru klasörü, CIK baştaki sıfırlar olmadan, accession tiresiz."""
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"


# ---------------------------------------------------------------- şirketler
COMPANIES: dict[str, dict] = {
    "EXMP": {
        "company": {"ticker": "EXMP", "cik": "0009990001", "name": "EXAMPLE CORP", "sic": "7372",
                    "sic_description": "Services-Prepackaged Software", "fiscal_year_end": "0630",
                    "exchanges": ["Nasdaq"]},
        "years": [2023, 2024, 2025, 2026],  # ilk yıl yalnızca ortalama/YoY tabanı
        "fy_end": (6, 30),
        "tenk_filed": {2024: "2024-07-30", 2025: "2025-07-29", 2026: "2026-07-29"},
        "facts": {
            "revenue": [178.2, 196.4, 224.9, 258.6],
            "cost_of_revenue": [55.9, 61.2, 68.7, 77.1],
            "operating_income": [74.1, 81.9, 99.8, 117.3],
            "net_income": [61.4, 66.9, 82.1, 93.7],
            "operating_cash_flow": [83.2, 85.6, 109.4, 124.8],
            "capex": [22.1, 27.4, 41.2, 58.3],
            "total_assets": [352.1, 398.6, 491.2, 572.4],
            "current_assets": [168.4, 176.9, 152.3, 171.6],
            "current_liabilities": [94.7, 102.1, 121.8, 139.5],
            "total_liabilities": [196.3, 204.9, 236.4, 259.8],
            "stockholders_equity": [155.8, 193.7, 254.8, 312.6],
        },
        "derived": {},
        "missing": {},
        "scale": B,
    },
    "NVLT": {
        "company": {"ticker": "NVLT", "cik": "0009990002", "name": "NOVALIGHT SYSTEMS INC", "sic": "3674",
                    "sic_description": "Semiconductors & Related Devices", "fiscal_year_end": "1231",
                    "exchanges": ["Nasdaq"]},
        "years": [2022, 2023, 2024, 2025],
        "fy_end": (12, 31),
        "tenk_filed": {2023: "2024-02-21", 2024: "2025-02-19", 2025: "2026-02-18"},
        "facts": {
            "revenue": [10.9, 12.8, 15.1, 19.7],
            "cost_of_revenue": [5.1, 5.9, 6.6, 8.2],
            "operating_income": [1.6, 2.1, 2.9, 4.4],
            "net_income": [1.2, 1.5, 2.3, 3.6],
            "operating_cash_flow": [1.9, 2.2, 3.1, 4.6],
            "capex": [1.4, 1.8, 2.6, 3.9],
            "total_assets": [18.4, 21.2, 25.6, 31.9],
            "current_assets": [7.9, None, 10.8, 13.7],
            "current_liabilities": [4.1, 4.6, 5.5, 7.2],
            "total_liabilities": [8.8, 10.4, 12.9, 16.1],
            "stockholders_equity": [9.6, 10.8, 12.7, 15.8],
        },
        "derived": {"total_liabilities"},
        "scale": B,
    },
    "HRBR": {
        "company": {"ticker": "HRBR", "cik": "0009990003", "name": "HARBOR FREIGHT LINES INC", "sic": "4213",
                    "sic_description": "Trucking (No Local)", "fiscal_year_end": "1231",
                    "exchanges": ["NYSE"]},
        "years": [2022, 2023, 2024, 2025],
        "fy_end": (12, 31),
        "tenk_filed": {2023: "2024-02-27", 2024: "2025-02-25", 2025: "2026-02-24"},
        "facts": {
            "revenue": [6.6, 6.2, 5.9, 5.5],
            "cost_of_revenue": [4.9, 4.7, 4.55, 4.35],
            "operating_income": [0.62, 0.48, 0.41, 0.33],
            "net_income": [0.41, 0.33, 0.27, 0.19],
            "operating_cash_flow": [0.88, 0.74, 0.61, 0.47],
            "capex": [0.61, 0.58, 0.49, 0.41],
            "total_assets": [7.9, 8.1, 8.3, 8.2],
            "current_assets": [2.1, 2.0, 1.9, 1.75],
            "current_liabilities": [1.2, 1.25, 1.3, 1.3],
            "total_liabilities": [3.8, 3.95, 4.15, 4.2],
            "stockholders_equity": [4.1, 4.15, 4.15, 4.0],
        },
        "derived": {"gross_profit"},
        "scale": B,
    },
    "QNTA": {
        "company": {"ticker": "QNTA", "cik": "0009990004", "name": "QUANTA RIDGE FOODS CO", "sic": "2000",
                    "sic_description": "Food and Kindred Products", "fiscal_year_end": "0930",
                    "exchanges": ["NYSE"]},
        "years": [2022, 2023, 2024, 2025],
        "fy_end": (9, 30),
        "tenk_filed": {2023: "2023-11-21", 2024: "2024-11-19", 2025: "2025-11-20"},
        "facts": {
            "revenue": [14.2, 14.9, 15.6, 16.1],
            "cost_of_revenue": [9.8, 10.4, 10.8, 11.1],
            "operating_income": [1.7, 1.8, 1.95, 2.0],
            "net_income": [1.1, 1.15, 1.28, 1.31],
            "operating_cash_flow": [1.5, 1.6, 1.75, 1.82],
            "capex": [0.6, 0.65, 0.62, 0.55],
            "total_assets": [21.0, 21.8, 22.5, 23.1],
            "current_assets": [4.9, 5.1, 5.6, 6.4],
            "current_liabilities": [4.4, 4.5, 4.7, 4.9],
            "total_liabilities": [12.6, 12.9, 13.1, 13.3],
            "stockholders_equity": [8.4, 8.9, 9.4, 9.8],
        },
        "derived": set(),
        "scale": B,
    },
    # CDRW: örnek verisi var ama başlangıçta hiç işlenmemiş (sunucu 404 döner); "SEC'ten çek
    # ve analiz et" akışını gösterir.
    "CDRW": {
        "company": {"ticker": "CDRW", "cik": "0009990009", "name": "CEDARWAY OUTDOOR INC", "sic": "3949",
                    "sic_description": "Sporting & Athletic Goods, NEC", "fiscal_year_end": "1231",
                    "exchanges": ["NYSE"]},
        "years": [2022, 2023, 2024, 2025],
        "fy_end": (12, 31),
        "tenk_filed": {2023: "2024-02-28", 2024: "2025-02-26", 2025: "2026-02-25"},
        "facts": {
            "revenue": [3.1, 3.4, 3.6, 3.9],
            "cost_of_revenue": [1.8, 1.95, 2.05, 2.2],
            "operating_income": [0.36, 0.41, 0.43, 0.49],
            "net_income": [0.25, 0.29, 0.3, 0.35],
            "operating_cash_flow": [0.31, 0.37, 0.4, 0.44],
            "capex": [0.12, 0.14, 0.15, 0.17],
            "total_assets": [2.9, 3.2, 3.5, 3.8],
            "current_assets": [1.5, 1.65, 1.8, 1.95],
            "current_liabilities": [0.8, 0.85, 0.9, 0.95],
            "total_liabilities": [1.4, 1.5, 1.6, 1.7],
            "stockholders_equity": [1.5, 1.7, 1.9, 2.1],
        },
        "derived": set(),
        "scale": B,
    },
}

# Metin analizleri: (form-yılı -> bölüm) duygu sayıları ve örnek cümleler
SENTIMENT = {
    "EXMP": {
        "model": "ProsusAI/finbert",
        "sections": [
            (2025, "1A", 640, 38, 402, [
                ("Our cloud services may experience outages, data losses and disruptions that could harm our reputation and financial results.", 0.97),
                ("We may not realize the anticipated benefits of our acquisitions, which could adversely affect our operating results.", 0.95),
                ("Adverse economic or market conditions may harm our business.", 0.93),
            ], [
                ("Our diversified portfolio of products helps mitigate the impact of weakness in any single market.", 0.71),
            ]),
            (2025, "7", 790, 198, 151, [
                ("Operating expenses increased due to investments in cloud engineering and data center capacity.", 0.88),
                ("Foreign currency movements decreased revenue growth by approximately two points.", 0.86),
            ], [
                ("Revenue increased 14% driven by growth in our cloud and productivity offerings.", 0.96),
                ("Gross margin percentage increased slightly, reflecting improvement in cloud services.", 0.92),
            ]),
            (2026, "1A", 668, 41, 389, [
                ("Constraints in the supply of AI accelerators and data center capacity could delay our cloud expansion and increase costs.", 0.97),
                ("Evolving regulation of artificial intelligence could require significant changes to our products and expose us to penalties.", 0.96),
                ("Cyberattacks, including those that use generative AI tools, could lead to reduced revenue and increased costs.", 0.95),
                ("The loss of one or more large multi-year enterprise commitments could adversely affect our results.", 0.93),
            ], [
                ("Our investments in security engineering strengthen our ability to detect and respond to threats.", 0.68),
            ]),
            (2026, "7", 812, 210, 140, [
                ("Capital expenditures increased significantly to support demand for AI infrastructure, reducing free cash flow growth.", 0.91),
                ("Hardware revenue decreased 6% due to lower device demand.", 0.89),
                ("Operating expenses increased 9% driven by headcount growth in engineering.", 0.82),
            ], [
                ("Revenue increased 15% to $258.6 billion, driven by strong demand for our cloud platform.", 0.97),
                ("Operating income increased 18%, reflecting revenue growth and continued cost discipline.", 0.96),
                ("Commercial remaining performance obligation increased 21%, providing visibility into future revenue.", 0.93),
            ]),
        ],
    },
    "NVLT": {
        "model": "ProsusAI/finbert",
        "sections": [
            (2025, "1A", 412, 22, 268, [
                ("We depend on a single foundry partner for the manufacture of our most advanced photonic chips.", 0.97),
                ("Export control regulations could restrict our ability to sell products to customers in certain regions.", 0.96),
                ("Demand for our products is concentrated among a small number of hyperscale data center customers.", 0.93),
            ], [
                ("Our long-term supply agreements provide improved visibility into wafer capacity.", 0.66),
            ]),
            (2025, "7", 534, 176, 92, [
                ("Inventory provisions increased as we transitioned customers to our next-generation product line.", 0.87),
            ], [
                ("Revenue grew 30% year over year, driven by data center optical interconnect products.", 0.97),
                ("Gross margin expanded to 58.4% due to favorable product mix and manufacturing yields.", 0.95),
                ("Operating cash flow increased to $4.6 billion.", 0.91),
            ]),
        ],
    },
    "HRBR": {
        "model": "ProsusAI/finbert",
        "sections": [
            (2024, "1A", 298, 12, 214, [
                ("A prolonged downturn in freight demand could reduce our volumes and pricing.", 0.96),
                ("We may be unable to attract and retain a sufficient number of qualified drivers.", 0.95),
            ], [
                ("Our modern tractor fleet supports lower maintenance costs.", 0.62),
            ]),
            (2024, "7", 356, 71, 98, [
                ("Revenue decreased 4.8% due to lower shipment volumes and weaker spot rates.", 0.94),
                ("Insurance and claims expense increased as a percentage of revenue.", 0.9),
            ], [
                ("Fuel efficiency initiatives reduced fuel cost per mile.", 0.84),
            ]),
            (2025, "1A", 321, 10, 236, [
                ("Rising insurance premiums and nuclear verdicts could materially increase our claims costs.", 0.97),
                ("Stricter emissions standards may require costly fleet replacements before the end of their useful lives.", 0.95),
                ("Our credit agreement contains covenants that may restrict our ability to operate our business.", 0.94),
            ], [
                ("Our liquidity position provides flexibility to manage through the freight cycle.", 0.64),
            ]),
            (2025, "7", 372, 61, 106, [
                ("Revenue decreased 6.8% to $5.5 billion as freight volumes remained soft.", 0.95),
                ("Operating ratio deteriorated by 100 basis points.", 0.93),
                ("Net income decreased 30% compared with the prior year.", 0.92),
            ], [
                ("Dedicated contract revenue increased 5%, partially offsetting declines in truckload.", 0.88),
                ("We reduced net capital expenditures by 16% while maintaining fleet age targets.", 0.8),
            ]),
        ],
    },
    "QNTA": {
        "model": "ProsusAI/finbert",
        "sections": [
            (2024, "1A", 355, 18, 221, [
                ("Volatility in the cost of agricultural commodities could reduce our margins.", 0.95),
            ], [
                ("Our hedging program reduces near-term exposure to commodity price changes.", 0.7),
            ]),
            (2024, "7", 448, 128, 96, [
                ("Volume declined 1% as price increases affected consumer demand.", 0.86),
            ], [
                ("Net sales increased 4.7% driven by favorable pricing.", 0.94),
            ]),
            (2025, "1A", 368, 20, 228, [
                ("Changes in consumer preferences, including increased use of weight-management medications, could reduce demand for our snack products.", 0.96),
                ("Consolidation in the retail grocery industry could increase our customer concentration.", 0.92),
            ], [
                ("Our portfolio of trusted brands supports retailer relationships.", 0.69),
            ]),
            (2025, "7", 461, 139, 94, [
                ("Selling, general and administrative expenses increased due to higher advertising investment.", 0.83),
            ], [
                ("Net sales increased 3.2% to $16.1 billion, with growth in the frozen and beverages segments.", 0.95),
                ("Adjusted gross margin improved 30 basis points due to productivity savings.", 0.9),
            ]),
        ],
    },
}

SENTIMENT["CDRW"] = {
    "model": "ProsusAI/finbert",
    "sections": [
        (2024, "1A", 244, 9, 171, [
            ("Unfavorable weather conditions could reduce demand for our seasonal products.", 0.95),
        ], [
            ("Our direct-to-consumer channel provides valuable insight into customer preferences.", 0.66),
        ]),
        (2024, "7", 302, 96, 61, [
            ("Freight costs increased due to longer ocean shipping routes.", 0.85),
        ], [
            ("Net sales increased 5.9% driven by growth in our camping and trail categories.", 0.94),
        ]),
        (2025, "1A", 259, 10, 180, [
            ("Tariffs on products sourced from Asia could increase our costs and reduce our margins.", 0.96),
            ("A significant portion of our sales is concentrated among a few large sporting goods retailers.", 0.93),
        ], [
            ("Diversifying our supplier base reduces our dependence on any single country.", 0.67),
        ]),
        (2025, "7", 318, 112, 58, [
            ("Inventory levels increased as we built stock ahead of anticipated tariff changes.", 0.84),
        ], [
            ("Net sales increased 8.3% to $3.9 billion, led by strong direct-to-consumer growth.", 0.96),
            ("Operating income increased 14% as gross margin expanded 60 basis points.", 0.93),
        ]),
    ],
}

RISK_CHANGES = {
    "EXMP": {
        "unchanged": 112,
        "changes": [
            ("added", None, "We are increasingly dependent on third-party providers of specialized AI accelerators and data center capacity. Supply constraints, export restrictions, or price increases affecting these components could delay the expansion of our cloud services and increase our operating costs.", None),
            ("added", None, "New and evolving regulations governing artificial intelligence, including obligations related to transparency, risk assessment and model governance, could require significant changes to our products and expose us to fines or litigation if we fail to comply.", None),
            ("added", None, "A material portion of our revenue now derives from multi-year commitments by large enterprise customers. The loss or non-renewal of one or more of these commitments could adversely affect our results of operations.", None),
            ("modified", "Our international operations subject us to risks from changes in foreign currency exchange rates, which could reduce our reported revenue and operating income.", "Our international operations subject us to significant risks from changes in foreign currency exchange rates and capital controls, which could materially reduce our reported revenue, operating income and cash flows.", 0.78),
            ("modified", "Cyberattacks and security vulnerabilities could lead to reduced revenue, increased costs, liability claims, or harm to our reputation.", "Cyberattacks, including attacks by nation-state actors and attacks that use generative AI tools, and security vulnerabilities could lead to reduced revenue, increased costs, regulatory penalties, liability claims, or harm to our reputation.", 0.69),
            ("modified", "We face intense competition across all markets for our products and services, which may lead to lower revenue or operating margins.", "We face intense and rapidly evolving competition across all markets for our products and services, including from new entrants offering AI-based alternatives, which may lead to lower revenue or operating margins.", 0.74),
            ("modified", "Our hardware products depend on a limited number of contract manufacturers located primarily in Asia.", "Our hardware and devices depend on a limited number of contract manufacturers, some of which are located in regions affected by geopolitical tension and trade restrictions.", 0.61),
            ("removed", "The transition of our workforce to hybrid working arrangements may disrupt our operations and make it more difficult to retain key employees.", None, None),
        ],
    },
    "HRBR": {
        "unchanged": 41,
        "changes": [
            ("added", None, "Insurance premiums and the cost of resolving large accident claims have increased significantly. Verdicts in excess of our insurance coverage could materially and adversely affect our financial condition.", None),
            ("added", None, "New greenhouse gas emission standards for heavy-duty vehicles may require us to purchase more expensive equipment or retire tractors before the end of their useful lives.", None),
            ("added", None, "Our credit agreement requires us to maintain a maximum leverage ratio. A further decline in earnings could cause us to breach this covenant.", None),
            ("added", None, "Cargo theft, including fictitious pickups facilitated by identity fraud, has increased across the industry and could result in losses not fully covered by insurance.", None),
            ("modified", "Fluctuations in the price or availability of diesel fuel could adversely affect our results of operations.", "Fluctuations in the price or availability of diesel fuel, and the reduced effectiveness of our fuel surcharge programs during periods of excess capacity, could adversely affect our results of operations.", 0.72),
            ("modified", "We may be unable to attract and retain qualified drivers, which could increase our costs.", "We may be unable to attract and retain qualified drivers and technicians, which could increase our compensation costs and limit our ability to grow dedicated operations.", 0.66),
            ("modified", "The trucking industry is highly competitive and fragmented.", "The trucking industry is highly competitive and fragmented, and excess capacity during the current freight downturn has put sustained pressure on our rates.", 0.58),
            ("removed", "Our planned acquisition of a regional less-than-truckload carrier may not be completed on the expected timeline.", None, None),
            ("removed", "Supply chain disruptions may delay deliveries of new tractors and trailers.", None, None),
        ],
    },
    "QNTA": {
        "unchanged": 57,
        "changes": [
            ("added", None, "Changes in consumer eating habits, including the increased use of GLP-1 weight-management medications, could reduce demand for certain of our snack and dessert products.", None),
            ("added", None, "Tariffs on imported packaging materials and ingredients could increase our costs, and we may not be able to pass these increases on to customers.", None),
            ("modified", "Volatility in the cost of agricultural commodities could reduce our margins.", "Volatility in the cost of agricultural commodities, including cocoa and vegetable oils, could reduce our margins if our hedging and pricing actions are insufficient.", 0.7),
            ("modified", "We depend on a limited number of large retail customers.", "We depend on a limited number of large retail customers, and further consolidation in the grocery industry could increase their bargaining power.", 0.67),
            ("removed", "Disruptions related to the implementation of our new enterprise resource planning system could affect our operations.", None, None),
        ],
    },
}

RISK_CHANGES["CDRW"] = {
    "unchanged": 38,
    "changes": [
        ("added", None, "Tariffs and other trade restrictions on products sourced from Asia could increase our costs, and we may be unable to offset these increases through pricing.", None),
        ("added", None, "A significant portion of our net sales is concentrated among a small number of large sporting goods retailers, and the loss of any of them could adversely affect our results.", None),
        ("modified", "Unfavorable weather conditions could reduce demand for our products.", "Unfavorable or unseasonable weather conditions, including warmer winters, could reduce demand for our seasonal products and increase inventory markdowns.", 0.71),
        ("removed", "Disruptions at our primary distribution center during its expansion could delay shipments.", None, None),
    ],
}

ASK_CDRW = [
    {"keywords": ["tedarik", "risk", "gümrük", "tarife"],
     "answer": "Şirket, Asya'dan tedarik edilen ürünlere uygulanan gümrük vergilerinin maliyetleri artırıp marjları düşürebileceğini yeni bir risk olarak eklemiştir [1]. Satışların önemli bir kısmının birkaç büyük spor malzemesi perakendecisinde yoğunlaştığı da belirtilmektedir [2].",
     "sources": [(1, "1A", 2025, 0, 0.72), (2, "1A", 2025, 1, 0.63)]},
    {"keywords": ["gelir", "büyüme", "satış", "kâr"],
     "answer": "Net satışlar, doğrudan tüketiciye satış kanalındaki güçlü büyümeyle %8,3 artarak 3,9 milyar dolara ulaşmıştır [1]. Faaliyet kârı, brüt marjın 60 baz puan genişlemesiyle %14 artmıştır [2].",
     "sources": [(1, "7", 2025, "pos0", 0.71), (2, "7", 2025, "pos1", 0.64)]},
]

ASK = {
    "EXMP": [
        {"keywords": ["tedarik", "supply", "çip", "donanım", "risk"],
         "answer": "Şirket, en son 10-K raporunda özel yapay zekâ hızlandırıcıları ve veri merkezi kapasitesi sağlayan üçüncü taraflara bağımlılığının arttığını yeni bir risk olarak eklemiştir; tedarik kısıtları veya ihracat kısıtlamaları bulut hizmetlerinin genişlemesini geciktirebilir [1]. Önceki yıl donanım üretimi yalnızca Asya'daki sözleşmeli üreticilerle ilişkilendirilirken, yeni metinde jeopolitik gerilim ve ticaret kısıtlamalarından etkilenen bölgeler açıkça belirtilmektedir [2].",
         "sources": [(1, "1A", 2026, 0, 0.71), (2, "1A", 2026, 6, 0.64)]},
        {"keywords": ["gelir", "büyüme", "revenue", "satış", "nakit"],
         "answer": "Yönetimin değerlendirmesine göre gelir, bulut platformuna yönelik güçlü talep sayesinde %15 artarak 258,6 milyar dolara ulaşmıştır [1]. Faaliyet kârı ise gelir büyümesi ve maliyet disiplini sayesinde %18 artmıştır; ancak yapay zekâ altyapısına yönelik yatırım harcamalarının belirgin biçimde arttığı da belirtilmektedir [2].",
         "sources": [(1, "7", 2026, "pos0", 0.69), (2, "7", 2026, "neg0", 0.61)]},
    ],
    "NVLT": [
        {"keywords": ["tedarik", "risk", "supply", "üretim"],
         "answer": "Şirket, en gelişmiş fotonik çiplerinin üretiminde tek bir dökümhane ortağına bağımlı olduğunu belirtmektedir [1]. Ayrıca talebin az sayıda büyük veri merkezi müşterisinde yoğunlaştığı ve bunun bir risk olarak raporlandığı görülmektedir [2].",
         "sources": [(1, "1A", 2025, "neg0", 0.74), (2, "1A", 2025, "neg2", 0.63)]},
        {"keywords": ["gelir", "büyüme", "marj", "nakit"],
         "answer": "Gelir, veri merkezi optik bağlantı ürünlerinin etkisiyle yıllık %30 artmıştır [1]. Brüt marj, ürün karması ve üretim verimi sayesinde %58,4'e yükselmiştir [2].",
         "sources": [(1, "7", 2025, "pos0", 0.72), (2, "7", 2025, "pos1", 0.66)]},
    ],
    "HRBR": [
        {"keywords": ["tedarik", "risk", "sigorta", "yakıt", "sürücü"],
         "answer": "Son 10-K raporunda sigorta primlerinin ve büyük kaza tazminatlarının maliyetinin belirgin biçimde arttığı yeni bir risk olarak eklenmiştir [1]. Kredi sözleşmesindeki azami kaldıraç oranı şartının, kazançların daha da düşmesi hâlinde ihlal edilebileceği de belirtilmektedir [2].",
         "sources": [(1, "1A", 2025, 0, 0.7), (2, "1A", 2025, 2, 0.62)]},
        {"keywords": ["gelir", "büyüme", "satış", "nakit", "kâr"],
         "answer": "Gelir, navlun hacimlerinin zayıf seyretmesi nedeniyle %6,8 azalarak 5,5 milyar dolara gerilemiştir [1]. Buna karşın tahsisli sözleşme gelirleri %5 artarak yük taşımacılığındaki düşüşü kısmen dengelemiştir [2].",
         "sources": [(1, "7", 2025, "neg0", 0.73), (2, "7", 2025, "pos0", 0.64)]},
    ],
    "QNTA": [
        {"keywords": ["tedarik", "risk", "emtia", "maliyet"],
         "answer": "Şirket, kakao ve bitkisel yağlar dahil tarımsal emtia maliyetlerindeki dalgalanmanın, korunma ve fiyatlama adımları yetersiz kalırsa marjları düşürebileceğini belirtmektedir [1]. Ambalaj malzemeleri ve girdilere uygulanan gümrük vergileri de yeni bir risk olarak eklenmiştir [2].",
         "sources": [(1, "1A", 2025, 2, 0.68), (2, "1A", 2025, 1, 0.61)]},
        {"keywords": ["gelir", "büyüme", "satış"],
         "answer": "Net satışlar, dondurulmuş gıda ve içecek segmentlerindeki büyümeyle %3,2 artarak 16,1 milyar dolara ulaşmıştır [1]. Düzeltilmiş brüt marj verimlilik tasarrufları sayesinde 30 baz puan iyileşmiştir [2].",
         "sources": [(1, "7", 2025, "pos0", 0.7), (2, "7", 2025, "pos1", 0.63)]},
    ],
    "CDRW": ASK_CDRW,
}

NO_TEXT_EXPLANATION = "Metin analizi henüz yapılmadı; bileşen hesaplanamadı ve ağırlığı diğer bileşenlere dağıtıldı."

ADVICE_REFUSAL = (
    "FinSight AI yatırım tavsiyesi vermez; aşağıda yalnızca raporlarda yer alan bilgiler "
    "özetlenmiştir."
)

SECTION_TITLES = {"1A": "Risk Faktörleri", "7": "Yönetimin Değerlendirmesi (MD&A)",
                  "2": "Yönetimin Değerlendirmesi (MD&A)"}


# ---------------------------------------------------------------- üretim
def build_filings(tk: str, spec: dict) -> list[dict]:
    cik = spec["company"]["cik"]
    years = spec["years"][1:]
    m, d = spec["fy_end"]
    out: list[dict] = []
    seq = 100
    for y in years:
        filed = spec["tenk_filed"][y]
        seq += 37
        acc = accession(cik, int(filed[:4]), seq)
        out.append({"accession_no": acc, "form": "10-K", "filing_date": filed,
                    "report_date": f"{y}-{m:02d}-{d:02d}", "items": [], "item_labels": [],
                    "url": filing_url(cik, acc), "analyzed": y >= years[-2]})
        # 10-K ile aynı gün 8-K (2.02 + 9.01)
        seq += 1
        acc8 = accession(cik, int(filed[:4]), seq)
        out.append({"accession_no": acc8, "form": "8-K", "filing_date": filed, "report_date": filed,
                    "items": ["2.02", "9.01"],
                    "item_labels": [ITEM_LABELS["2.02"], ITEM_LABELS["9.01"]],
                    "url": filing_url(cik, acc8), "analyzed": False})
    # son mali yıldan sonraki çeyrekler (10-Q)
    last = years[-1]
    quarter_ends = {
        (6, 30): [(last, 9, 30, f"{last}-10-28"), (last + 1, 12, 31, f"{last + 1}-01-27"),
                  (last + 1, 3, 31, f"{last + 1}-04-28"), (last, 9, 30, None)],
        (12, 31): [(last + 1, 3, 31, f"{last + 1}-04-29"), (last + 1, 6, 30, f"{last + 1}-07-29"),
                   (last + 1, 9, 30, f"{last + 1}-10-28")],
        (9, 30): [(last, 12, 31, f"{last + 1}-02-04"), (last + 1, 3, 31, f"{last + 1}-05-06"),
                  (last + 1, 6, 30, f"{last + 1}-08-05")],
    }[spec["fy_end"]]
    if spec["fy_end"] == (6, 30):
        # EXMP: FY2026 10-K Temmuz 2026'da; ardından FY2027 1. çeyrek
        quarter_ends = [(last - 1, 9, 30, f"{last - 1}-10-29"), (last - 1, 12, 31, f"{last}-01-28"),
                        (last, 3, 31, f"{last}-04-29"), (last, 9, 30, f"{last}-10-28")]
    for (yy, mm, dd, filed) in quarter_ends:
        if not filed:
            continue
        seq += 29
        acc = accession(cik, int(filed[:4]), seq)
        out.append({"accession_no": acc, "form": "10-Q", "filing_date": filed,
                    "report_date": f"{yy}-{mm:02d}-{dd:02d}", "items": [], "item_labels": [],
                    "url": filing_url(cik, acc), "analyzed": False})
    # birkaç ek 8-K
    extra = {
        "EXMP": [("2026-03-12", ["5.02"]), ("2025-12-04", ["5.07"]), ("2026-09-16", ["1.01", "9.01"]),
                 ("2026-10-28", ["2.02", "9.01"])],
        "NVLT": [("2026-04-29", ["2.02", "9.01"]), ("2026-06-11", ["5.07"]), ("2026-08-20", ["7.01", "9.01"])],
        "HRBR": [("2026-01-15", ["8.01"]), ("2026-05-14", ["5.02"]), ("2026-07-29", ["2.02", "9.01"]),
                 ("2026-10-28", ["2.02", "9.01"])],
        "QNTA": [("2026-01-22", ["5.07"]), ("2026-08-05", ["2.02", "9.01"])],
        "CDRW": [("2026-05-07", ["5.07"]), ("2026-07-30", ["2.02", "9.01"])],
    }[tk]
    for filed, items in extra:
        seq += 11
        acc = accession(cik, int(filed[:4]), seq)
        out.append({"accession_no": acc, "form": "8-K", "filing_date": filed, "report_date": filed,
                    "items": items, "item_labels": [ITEM_LABELS[i] for i in items],
                    "url": filing_url(cik, acc), "analyzed": False})
    out.sort(key=lambda f: (f["filing_date"], f["accession_no"]), reverse=True)
    return out


def ref(f: dict) -> dict:
    return {k: f[k] for k in ("accession_no", "form", "filing_date", "report_date", "url")}


def build(tk: str, spec: dict) -> dict:
    company = spec["company"]
    cik = company["cik"]
    m, d = spec["fy_end"]
    years = spec["years"]
    scale = spec["scale"]
    filings = build_filings(tk, spec)
    tenk_by_year = {}
    for f in filings:
        if f["form"] == "10-K":
            tenk_by_year[int(f["report_date"][:4])] = f

    raw = {k: [None if v is None else round(v * scale) for v in vals] for k, vals in spec["facts"].items()}
    raw["gross_profit"] = [r - c for r, c in zip(raw["revenue"], raw["cost_of_revenue"])]

    def pend(y: int) -> str:
        return f"{y}-{m:02d}-{d:02d}"

    def pstart(y: int) -> str:
        if (m, d) == (12, 31):
            return f"{y}-01-01"
        nm = m + 1
        return f"{y - 1}-{nm:02d}-01"

    def source(metric: str, y: int) -> dict:
        f = tenk_by_year[y]
        derived = metric in spec["derived"]
        return {"xbrl_tag": "derived" if derived else TAGS[metric], "form": "10-K",
                "accession_no": f["accession_no"], "filed": f["filing_date"],
                "derived_from": DERIVED[metric] if derived else None,
                "url": folder_url(cik, f["accession_no"])}

    def yoy(metric: str, i: int) -> float | None:
        cur, prev = raw[metric][i], raw[metric][i - 1] if i > 0 else None
        if cur is None or prev is None or prev == 0:
            return None
        return round((cur - prev) / abs(prev), 4)

    shown = list(range(1, len(years)))
    facts_metrics = []
    for metric, label in METRIC_LABELS.items():
        kind = "duration" if metric in DURATION else "instant"
        vals = []
        for i in shown:
            v = raw[metric][i]
            if v is None:
                continue  # eksik veri: gözlem yok
            item = {"period_start": pstart(years[i]) if kind == "duration" else None,
                    "period_end": pend(years[i]), "value": v, "yoy": yoy(metric, i),
                    "source": source(metric, years[i])}
            vals.append(item)
        facts_metrics.append({"metric": metric, "label": label, "kind": kind, "values": vals})

    # ---- oranlar
    def g(metric: str, i: int):
        return raw[metric][i]

    ratio_rows = []
    latest_ratio: dict[str, float | None] = {}
    for key, label, formula, unit, hib in RATIOS:
        values = []
        for i in shown:
            y = years[i]
            acc = tenk_by_year[y]["accession_no"]
            val, status, reason, note, inputs = None, "ok", None, None, []

            def inp(mt: str, v):
                inputs.append({"metric": mt, "value": v, "accession_no": acc})

            try:
                if key in ("gross_margin", "operating_margin", "net_margin", "fcf_margin"):
                    num_metric = {"gross_margin": "gross_profit", "operating_margin": "operating_income",
                                  "net_margin": "net_income"}.get(key)
                    rev = g("revenue", i)
                    inp("revenue", rev)
                    if key == "fcf_margin":
                        ocf, cap = g("operating_cash_flow", i), g("capex", i)
                        inp("operating_cash_flow", ocf)
                        inp("capex", cap)
                        val = (ocf - cap) / rev
                    else:
                        num = g(num_metric, i)
                        inp(num_metric, num)
                        val = num / rev
                elif key in ("roa", "roe"):
                    base = "total_assets" if key == "roa" else "stockholders_equity"
                    ni = g("net_income", i)
                    cur, prev = g(base, i), g(base, i - 1)
                    inp("net_income", ni)
                    inp(base, cur)
                    if key == "roe" and cur <= 0:
                        raise ValueError("özkaynak negatif veya sıfır; oran anlamlı değil")
                    avg = (cur + prev) / 2
                    val = ni / avg
                elif key == "current_ratio":
                    ca, cl = g("current_assets", i), g("current_liabilities", i)
                    if ca is None:
                        raise ValueError("eksik veri: current_assets")
                    inp("current_assets", ca)
                    inp("current_liabilities", cl)
                    val = ca / cl
                elif key == "debt_to_equity":
                    tl, eq = g("total_liabilities", i), g("stockholders_equity", i)
                    inp("total_liabilities", tl)
                    inp("stockholders_equity", eq)
                    val = tl / eq
                elif key == "liabilities_to_assets":
                    tl, ta = g("total_liabilities", i), g("total_assets", i)
                    inp("total_liabilities", tl)
                    inp("total_assets", ta)
                    val = tl / ta
                elif key == "ocf_to_net_income":
                    ocf, ni = g("operating_cash_flow", i), g("net_income", i)
                    inp("operating_cash_flow", ocf)
                    inp("net_income", ni)
                    if ni <= 0:
                        raise ValueError("net kâr sıfır veya negatif; oran anlamlı değil")
                    val = ocf / ni
            except ValueError as exc:
                val, status, reason = None, "missing", str(exc)
            values.append({"period_end": pend(y), "value": None if val is None else round(val, 4),
                           "status": status, "reason": reason, "note": note, "inputs": inputs})
        latest_ratio[key] = values[-1]["value"]
        ratio_rows.append({"key": key, "label": label, "formula": formula, "unit": unit,
                           "higher_is_better": hib, "values": values})

    # ---- duygu
    sent_spec = SENTIMENT[tk]
    sections = []
    for (y, code, total, pos, neg, top_neg, top_pos) in sent_spec["sections"]:
        neu = total - pos - neg
        sections.append({"filing": ref(tenk_by_year[y]), "item_code": code, "title": SECTION_TITLES[code],
                         "sentence_count": total, "positive": pos, "negative": neg, "neutral": neu,
                         "net_tone": round((pos - neg) / total, 3),
                         "top_negative": [{"sentence": s, "score": sc} for s, sc in top_neg],
                         "top_positive": [{"sentence": s, "score": sc} for s, sc in top_pos]})
    sections.sort(key=lambda s: (s["filing"]["filing_date"], s["item_code"]))
    latest_year = years[-1]
    latest_secs = {s["item_code"]: s for s in sections if s["filing"]["report_date"].startswith(str(latest_year))}
    mdna_tone = latest_secs["7"]["net_tone"] if "7" in latest_secs else None
    risk_tone = latest_secs["1A"]["net_tone"] if "1A" in latest_secs else None

    # ---- risk değişimleri
    rc = RISK_CHANGES.get(tk)
    if rc:
        changes = [{"change_type": t, "old_text": o, "new_text": n, "similarity": s}
                   for (t, o, n, s) in rc["changes"]]
        order = {"added": 0, "modified": 1, "removed": 2}
        changes.sort(key=lambda c: order[c["change_type"]])
        counts = {"added": sum(c["change_type"] == "added" for c in changes),
                  "removed": sum(c["change_type"] == "removed" for c in changes),
                  "modified": sum(c["change_type"] == "modified" for c in changes),
                  "unchanged": rc["unchanged"]}
        risk_changes = {"old_filing": ref(tenk_by_year[latest_year - 1]),
                        "new_filing": ref(tenk_by_year[latest_year]),
                        "counts": counts, "changes": changes, "disclaimer": DISCLAIMER}
        risk_summary = dict(counts)
    else:
        risk_changes = {"old_filing": None, "new_filing": ref(tenk_by_year[latest_year]),
                        "counts": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
                        "changes": [], "disclaimer": DISCLAIMER}
        risk_summary = None

    # ---- skor (with_text=False: yalnızca finansal veri çekilmiş, metin analizi yok)
    def make_score(with_text: bool) -> dict:
        L = latest_ratio
        comps = []

        def indicator(key: str, label: str, value, display: str, points: float) -> dict:
            return {"key": key, "label": label, "value": value, "display": display, "points": r1(points)}

        def comp(key, label, weight, inds, explanation, available=True, override=None):
            pts = [i["points"] for i in inds]
            score = override if override is not None else (sum(pts) / len(pts) if pts else None)
            comps.append({"key": key, "label": label, "weight": weight,
                          "score": r1(score) if (available and score is not None) else None,
                          "available": available and score is not None, "indicators": inds,
                          "explanation": explanation})

        def pct_ind(key, label, a, b):
            v = L[key]
            return indicator(key, label, v, tr_pct(v), lin(v, a, b))

        inds = [pct_ind("net_margin", "Net kâr marjı", 0, 0.20), pct_ind("operating_margin", "Faaliyet kâr marjı", 0, 0.25),
                pct_ind("roa", "ROA", 0, 0.15), pct_ind("roe", "ROE", 0, 0.25)]
        comp("profitability", "Kârlılık", 25, inds,
             "; ".join(f"{i['label']} {i['display']} → {tr_num(i['points'])} puan" for i in inds)
             + ". Eşikler: net marj %0–%20, faaliyet marjı %0–%25, ROA %0–%15, ROE %0–%25.")
        de, la = L["debt_to_equity"], L["liabilities_to_assets"]
        inds = [indicator("debt_to_equity", "Borç / özkaynak", de, tr_num(de, 2), lin(de, 3.0, 0.5)),
                indicator("liabilities_to_assets", "Yükümlülük / varlık", la, tr_pct(la), lin(la, 0.9, 0.3))]
        comp("leverage", "Kaldıraç ve borç", 20, inds,
             f"Borç/özkaynak {tr_num(de, 2)} (eşik 3,0–0,5, ters → {tr_num(inds[0]['points'])} puan); "
             f"yükümlülük/varlık {tr_pct(la)} (eşik %90–%30, ters → {tr_num(inds[1]['points'])} puan).")
        cr = L["current_ratio"]
        inds = [indicator("current_ratio", "Cari oran", cr, tr_num(cr, 2), lin(cr, 0.5, 2.0))]
        comp("liquidity", "Likidite", 15, inds,
             f"Cari oran {tr_num(cr, 2)} (eşik 0,5–2,0 → {tr_num(inds[0]['points'])} puan).")
        oc, fm = L["ocf_to_net_income"], L["fcf_margin"]
        inds = [indicator("ocf_to_net_income", "Nakit akışı kalitesi", oc, tr_num(oc, 2), lin(oc, 0, 1.2)),
                indicator("fcf_margin", "Serbest nakit akışı marjı", fm, tr_pct(fm), lin(fm, -0.05, 0.15))]
        comp("cash_quality", "Nakit akışı kalitesi", 15, inds,
             f"İşletme nakit akışı / net kâr {tr_num(oc, 2)} (eşik 0–1,2 → {tr_num(inds[0]['points'])} puan); "
             f"serbest nakit akışı marjı {tr_pct(fm)} (eşik −%5–%15 → {tr_num(inds[1]['points'])} puan).")
        ry, ny = yoy("revenue", len(years) - 1), yoy("net_income", len(years) - 1)
        inds = [indicator("revenue_yoy", "Gelir değişimi", ry, tr_pct(ry), lin(ry, -0.10, 0.15)),
                indicator("net_income_yoy", "Net kâr değişimi", ny, tr_pct(ny), lin(ny, -0.20, 0.20))]
        comp("growth", "Büyüme", 10, inds,
             f"Gelir yıllık {tr_pct(ry)} (eşik −%10–%15 → {tr_num(inds[0]['points'])} puan); "
             f"net kâr yıllık {tr_pct(ny)} (eşik −%20–%20 → {tr_num(inds[1]['points'])} puan).")
        if not with_text:
            comp("text_tone", "Metin tonu", 10, [], NO_TEXT_EXPLANATION, available=False)
        elif mdna_tone is not None:
            inds = [indicator("mdna_net_tone", "MD&A net tonu", mdna_tone, tr_num(mdna_tone, 3), lin(mdna_tone, -0.40, 0.20))]
            comp("text_tone", "Metin tonu", 10, inds,
                 f"Son 10-K MD&A net tonu {tr_num(mdna_tone, 3)} (eşik −0,40–0,20 → {tr_num(inds[0]['points'])} puan).")
        else:
            comp("text_tone", "Metin tonu", 10, [], "MD&A ve risk bölümü bulunamadı; bileşen hesaplanamadı.", available=False)
        if not with_text:
            comp("risk_change", "Risk değişimi", 5, [], NO_TEXT_EXPLANATION, available=False)
        elif risk_summary:
            added = risk_summary["added"]
            inds = [indicator("risk_added", "Eklenen risk paragrafı", added, str(added), lin(added, 15, 0))]
            comp("risk_change", "Risk değişimi", 5, inds,
                 f"Son iki 10-K arasında {added} yeni risk paragrafı (eşik 15–0, ters → {tr_num(inds[0]['points'])} puan).")
        else:
            comp("risk_change", "Risk değişimi", 5, [],
                 "Karşılaştırılacak iki 10-K risk bölümü yok; bileşen hesaplanamadı ve ağırlığı diğer bileşenlere dağıtıldı.",
                 available=False)

        # nakit kalitesi özel kuralı: net kâr ≤ 0 ise
        avail = [c for c in comps if c["available"]]
        wsum = sum(c["weight"] for c in avail)
        total = r1(sum(c["weight"] * c["score"] for c in avail) / wsum)
        missing = [c["key"] for c in comps if not c["available"]]
        explanation = (f"Toplam skor {len(avail)} bileşenin ağırlıklı ortalamasıdır "
                       f"(kullanılabilen ağırlık {wsum}/100). Bant gösterim amaçlıdır; derecelendirme değildir.")
        score = {"total": total, "band": band(total), "as_of_period": pend(latest_year),
                 "weights_version": "v1-2026-11", "components": comps, "missing_components": missing,
                 "explanation": explanation, "disclaimer": DISCLAIMER}
        return score

    score = make_score(True)
    score_fin = make_score(False)

    # ---- genel bakış
    key_order = ["revenue", "net_income", "operating_cash_flow", "total_assets", "total_liabilities",
                 "stockholders_equity"]
    i_last = len(years) - 1
    key_metrics = [{"metric": k, "label": METRIC_LABELS[k], "value": raw[k][i_last],
                    "period_end": pend(latest_year), "yoy": yoy(k, i_last),
                    "source": source(k, latest_year)} for k in key_order]
    latest_10k = tenk_by_year[latest_year]
    overview = {
        "company": company,
        "analyzed": True,
        "score": {"total": score["total"], "band": score["band"], "as_of_period": score["as_of_period"]},
        "key_metrics": key_metrics,
        "latest_filings": [ref(f) for f in filings[:5]],
        "sentiment_summary": {"filing": ref(latest_10k), "mdna_net_tone": mdna_tone,
                              "risk_net_tone": risk_tone, "model": sent_spec["model"]},
        "risk_summary": risk_summary,
        "analysis_updated_at": "2026-11-17T21:40:00Z",
        "disclaimer": DISCLAIMER,
    }
    # Yalnızca finansal veri çekilmiş (metin analizi yok) durumu: ton/risk özeti yok, skor metinsiz
    overview_fin = {
        **overview,
        "analyzed": False,
        "score": {"total": score_fin["total"], "band": score_fin["band"],
                  "as_of_period": score_fin["as_of_period"]},
        "sentiment_summary": None,
        "risk_summary": None,
        "analysis_updated_at": None,
    }

    # ---- soru-cevap
    def section_of(year: int, code: str) -> dict:
        for s in sections:
            if s["item_code"] == code and s["filing"]["report_date"].startswith(str(year)):
                return s
        raise KeyError((year, code))

    ask = []
    chunk = 400 + int(cik[-1]) * 37
    for entry in ASK[tk]:
        srcs = []
        for (n, code, y, pick, sim) in entry["sources"]:
            s = section_of(y, code)
            if isinstance(pick, int):
                text = (rc["changes"][pick][2] or rc["changes"][pick][1]) if rc else s["top_negative"][0]["sentence"]
            else:
                kind, idx = ("top_positive" if pick.startswith("pos") else "top_negative"), int(pick[3:])
                text = s[kind][idx]["sentence"]
            chunk += 13
            srcs.append({"n": n, "chunk_id": chunk, "section": code, "section_title": SECTION_TITLES[code],
                         "form": s["filing"]["form"], "filing_date": s["filing"]["filing_date"],
                         "accession_no": s["filing"]["accession_no"], "url": s["filing"]["url"],
                         "text": text, "similarity": sim})
        ask.append({"keywords": entry["keywords"], "answer": entry["answer"], "sources": srcs})

    return {
        "company": company,
        "overview": overview,
        "overview_fin": overview_fin,
        "facts": {"company": company, "metrics": facts_metrics, "disclaimer": DISCLAIMER},
        "ratios": {"company": company, "periods": [pend(years[i]) for i in shown], "ratios": ratio_rows,
                   "disclaimer": DISCLAIMER},
        "score": score,
        "score_fin": score_fin,
        "filings": filings,
        "sentiment": {"model": sent_spec["model"], "sections": sections, "disclaimer": DISCLAIMER},
        "risk_changes": risk_changes,
        "ask": ask,
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    built = {tk: build(tk, spec) for tk, spec in COMPANIES.items()}
    for tk, data in built.items():
        (OUT / f"{tk}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(tk, data["score"]["total"], data["score"]["band"])

    # Arama dizini: işlenmiş + işlenmemiş kurgusal şirketler
    # Arama dizini ve başlangıç durumu ("state"):
    #   full = finansal veri + metin analizi, fin = yalnızca finansal veri (analyzed=false),
    #   none = hiç işlenmemiş (şirket uçları 404 döner; gerçek backend gibi).
    search = [
        {"ticker": "EXMP", "cik": "0009990001", "name": "EXAMPLE CORP", "state": "full"},
        {"ticker": "HRBR", "cik": "0009990003", "name": "HARBOR FREIGHT LINES INC", "state": "full"},
        {"ticker": "NVLT", "cik": "0009990002", "name": "NOVALIGHT SYSTEMS INC", "state": "full"},
        # QNTA: finansal verisi çekilmiş, metin analizi yapılmamış; "Metin analizini çalıştır"
        {"ticker": "QNTA", "cik": "0009990004", "name": "QUANTA RIDGE FOODS CO", "state": "fin"},
        # CDRW: örnek verisi var ama hiç işlenmemiş; "SEC'ten verileri çek ve analiz et"
        {"ticker": "CDRW", "cik": "0009990009", "name": "CEDARWAY OUTDOOR INC", "state": "none"},
        # Aşağıdakilerin örnek verisi yok; analiz işi demo modunda hata ile biter
        {"ticker": "LMNR", "cik": "0009990005", "name": "LUMENOR BIOTECH INC", "state": "none"},
        {"ticker": "ORVB", "cik": "0009990006", "name": "ORCHARD VALLEY BANCORP", "state": "none"},
        {"ticker": "PLSD", "cik": "0009990007", "name": "PLASMA DYNAMICS CORP", "state": "none"},
        {"ticker": "EXMR", "cik": "0009990008", "name": "EXAMPLE MARINE HOLDINGS", "state": "none"},
    ]
    meta = {
        "health": {"status": "ok", "version": "0.3.0-mock",
                   "models": {"sentiment": "ProsusAI/finbert",
                              "embedding": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                              "llm": {"provider": "anthropic", "model": "claude-haiku-5-5", "enabled": True}}},
        "search_index": search,
        "disclaimer": DISCLAIMER,
        "not_found_answer": "Bu bilgi işlenen raporlarda bulunamadı.",
        "advice_refusal": ADVICE_REFUSAL,
        "embedding_mismatch_warning": "Parçalar farklı bir gömme modeliyle oluşturulmuş; analizi yeniden çalıştırın.",
        # İşi durdurmayan adım uyarıları (jobs.warnings); şirket → uyarı listesi
        "job_warnings": {
            "CDRW": ["2026-05-07 tarihli 8-K belgesi indirilemedi; adım atlandı."],
        },
        "financial_steps": [
            "Şirket bilgileri alınıyor (submissions)",
            "XBRL finansal verileri çekiliyor (companyfacts)",
            "Oranlar hesaplanıyor",
            "Skor hesaplanıyor",
        ],
        "ingest_steps": [
            "Şirket bilgileri alınıyor (submissions)",
            "XBRL finansal verileri çekiliyor (companyfacts)",
            "10-K belgeleri indiriliyor",
            "Bölümler ayıklanıyor (Item 1A, Item 7)",
            "Oranlar hesaplanıyor",
            "Duygu analizi (10-K 2025)",
            "Risk bölümleri karşılaştırılıyor",
            "Gömme vektörleri oluşturuluyor",
            "Skor hesaplanıyor",
        ],
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
