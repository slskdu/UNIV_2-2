"""시장 인사이트 순수 로직 회귀 테스트. 실행: py -m unittest discover -s tests"""
import os
import unittest

os.environ.setdefault("TOSS_CLIENT_ID", "test")
os.environ.setdefault("TOSS_CLIENT_SECRET", "test")

from app.article_summary import build_summary, extract_article, google_article_id, is_korean, split_sentences  # noqa: E402
from app.briefing import compare_sector_flows  # noqa: E402
from app.history import (  # noqa: E402
    build_equal_weight_index,
    describe_warnings,
    detect_price_adjustments,
    summarize_trend,
)
from app.news_classifier import build_digest, classify_articles, classify_title, merge_ai_labels, tone_price_signal  # noqa: E402
from app.sector_news import clean_company_name, foreign_targets, is_relevant, merge_similar, parse_rss  # noqa: E402
from app.sectors import flow_label  # noqa: E402
from app.translator import parse_translation  # noqa: E402


def candles(closes):
    return [
        {"date": f"2026-01-{day:02d}", "open": close, "high": close, "low": close, "close": close, "volume": 0}
        for day, close in enumerate(closes, start=1)
    ]


class ClassifyTitleTest(unittest.TestCase):
    def test_positive_and_negative(self):
        self.assertEqual(classify_title("삼성전자 사상 최대 실적에 신고가")["tone"], "호재")
        self.assertEqual(classify_title("반도체 관련주 약세…SK하이닉스 5%대 하락")["tone"], "악재")
        self.assertEqual(classify_title("삼성전기, 첫 사내 해커톤 개최")["tone"], "중립")

    def test_compound_phrase_is_not_double_counted(self):
        result = classify_title("A사 적자 축소")
        self.assertEqual(result["tone"], "호재")
        self.assertEqual(result["tone_keywords"], ["적자 축소"])
        self.assertEqual(classify_title("증권가, B사 목표가 하향")["tone_score"], -2)

    def test_concession_uses_trailing_clause(self):
        self.assertEqual(classify_title("반도체 역대급 실적에도 급락, 왜?")["tone"], "악재")
        self.assertEqual(classify_title("관세 우려에도 조선주 강세")["tone"], "호재")

    def test_category(self):
        self.assertEqual(classify_title("C사 횡령 혐의로 압수수색")["category"], "리스크·사고")
        self.assertEqual(classify_title("D사 3분기 영업이익 증가")["category"], "실적")
        self.assertEqual(classify_title("E사 창립 기념 행사")["category"], "기타")

    def test_expanded_keywords(self):
        self.assertEqual(classify_title("F사 신제품 사전 예약 완판")["tone"], "호재")
        self.assertEqual(classify_title("G사 공장 가동 지연")["tone"], "악재")
        self.assertTrue(classify_title("H사, 美 수출 통제 대상 포함")["tone_strong"])
        self.assertEqual(classify_title("Chipmaker raises guidance")["tone_score"], 2)


class DigestTest(unittest.TestCase):
    def test_tone_and_signal(self):
        articles = classify_articles([
            {"title": "조선주 급락"}, {"title": "조선 업종 신저가 속출"},
            {"title": "조선 3사 목표가 하향"}, {"title": "조선소 방문 행사"},
        ])
        digest = build_digest(articles, "조선", 7)
        self.assertEqual((digest["positive"], digest["negative"], digest["neutral"]), (0, 3, 1))
        self.assertEqual(digest["tone"], "악재 우위")
        self.assertIn("일치", tone_price_signal(digest, -3.0, "분야 1주"))
        self.assertIn("올랐습니다", tone_price_signal(digest, 2.0, "분야 1주"))

    def test_few_articles_withholds_judgement(self):
        digest = build_digest(classify_articles([{"title": "조선주 급락"}]), "조선", 7)
        self.assertEqual(digest["tone"], "판단 보류")
        self.assertIsNone(tone_price_signal(digest, -3.0, "분야 1주"))
        self.assertEqual(build_digest([], "조선", 7)["article_count"], 0)


class AiLabelMergeTest(unittest.TestCase):
    def test_failed_batch_keeps_rule_labels(self):
        rule = classify_articles([{"title": "A사 급등"}, {"title": "B사 행사"}, {"title": "C사 급락"}, {"title": "D사 소식"}])
        merged, covered = merge_ai_labels(rule, [["악재", "호재"], None], batch_size=2)
        self.assertEqual(covered, 2)
        self.assertEqual([a["tone"] for a in merged], ["악재", "호재", "악재", "중립"])
        self.assertEqual([a["tone_source"] for a in merged], ["ai", "ai", "rule", "rule"])
        self.assertEqual(merged[0]["tone_keywords"], [])
        self.assertEqual(merged[2]["tone_keywords"], ["급락"])

    def test_strong_and_unrelated_labels(self):
        rule = classify_articles([{"title": f"기사 {i}"} for i in range(5)])
        merged, covered = merge_ai_labels(rule, [["강한 호재", "호재", "무관", "무관", "강한 악재"]], batch_size=20)
        self.assertEqual(covered, 5)
        self.assertEqual([(a["tone"], a["tone_score"], a["tone_strong"]) for a in merged][:3],
                         [("호재", 2, True), ("호재", 1, False), ("무관", 0, False)])
        digest = build_digest(merged, "A사", 7)
        self.assertEqual((digest["unrelated"], digest["relevant_count"]), (2, 3))
        self.assertEqual((digest["strong_positive"], digest["strong_negative"]), (1, 1))
        # 무관 기사는 비율 계산에서 빠집니다: (2 - 1) / 3
        self.assertAlmostEqual(digest["balance"], 0.333, places=3)
        self.assertIn("무관한 2건 제외", digest["summary"])

    def test_unknown_label_falls_back(self):
        merged, covered = merge_ai_labels(classify_articles([{"title": "A사 급등"}]), [["모름"]], batch_size=20)
        self.assertEqual((covered, merged[0]["tone"], merged[0]["tone_source"]), (0, "호재", "rule"))


class NewsParsingTest(unittest.TestCase):
    RSS = """<?xml version="1.0"?><rss><channel>
        <item><title>조선주 급락 - 매일경제 - 매일경제</title><link>https://example.com/a</link>
            <pubDate>Mon, 12 Oct 2026 06:35:28 GMT</pubDate><source url="https://mk.co.kr">매일경제</source></item>
        <item><title>링크가 http인 기사 - 가</title><link>http://example.com/b</link><source>가</source></item>
    </channel></rss>"""

    def test_parse_rss_strips_publisher_and_converts_to_kst(self):
        articles = parse_rss(self.RSS)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "조선주 급락")
        self.assertEqual(articles[0]["publisher"], "매일경제")
        self.assertEqual(articles[0]["published_at"], "2026-10-12 15:35")
        self.assertEqual(parse_rss("not xml"), [])

    def test_merge_similar_titles(self):
        merged = merge_similar([
            {"title": "이재용, 홍라희 삼성전자 주식 2.6조 증여받는다", "url": "https://e/1"},
            {"title": "이재용 회장, 홍라희 삼성전자 주식 2.6조 증여받아", "url": "https://e/2"},
            {"title": "삼성전자 차세대 메모리 전략 공개", "url": "https://e/3"},
        ])
        self.assertEqual([article["url"] for article in merged], ["https://e/1", "https://e/3"])
        self.assertEqual(merged[0]["similar_count"], 1)
        # 대표 기사를 열 수 없을 때 대신 요약할 다른 매체 기사 주소
        self.assertEqual(merged[0]["alternates"], ["https://e/2"])
        self.assertEqual(merged[1]["alternates"], [])


class RelevanceTest(unittest.TestCase):
    ADM_US = {"label": "아처 대니얼스 미들랜드", "names": ["Archer-Daniels-Midland"], "tickers": ["ADM"], "edition": "us"}

    def test_same_ticker_on_foreign_exchange_is_rejected(self):
        self.assertFalse(is_relevant("Why Did Admiral Group (LSE:ADM) Gain as Financials Rebounded", self.ADM_US))
        self.assertTrue(is_relevant("Archer-Daniels-Midland (NYSE:ADM) Shares Down 3.1%", self.ADM_US))
        self.assertTrue(is_relevant("ADM Stock Slides After Earnings", self.ADM_US))
        self.assertTrue(is_relevant("Archer Daniels Midland cuts outlook", self.ADM_US))
        self.assertFalse(is_relevant("ADMIRAL posts record profit", self.ADM_US))

    def test_bare_ticker_only_counts_in_us_edition(self):
        german = {**self.ADM_US, "edition": "de"}
        self.assertFalse(is_relevant("ADM Aktie legt zu", german))
        self.assertTrue(is_relevant("Archer-Daniels-Midland: Aktie unter Druck", german))

    def test_korean_label_default(self):
        self.assertTrue(is_relevant("조선주 급락", {"label": "조선"}))
        self.assertFalse(is_relevant("국민연금 경고", {"label": "조선"}))

    def test_company_name_cleanup_and_foreign_targets(self):
        self.assertEqual(clean_company_name("Samsung Electronics Co., Ltd."), "Samsung Electronics")
        self.assertEqual(clean_company_name("SK hynix Inc."), "SK hynix")
        self.assertEqual(clean_company_name("Hyundai Motor Company"), "Hyundai Motor")
        self.assertEqual(clean_company_name(None), "")
        korean = foreign_targets("삼성전자", "Samsung Electronics Co., Ltd.", "005930", us_listed=False)
        self.assertEqual([target["edition"] for target in korean], ["us", "gb", "in", "jp", "de", "fr"])
        self.assertTrue(all("tickers" not in target for target in korean))
        american = foreign_targets("엔비디아", "NVIDIA", "NVDA", us_listed=True)
        self.assertEqual([target["edition"] for target in american], ["us", "us", "gb", "in", "jp", "de", "fr"])
        self.assertEqual(foreign_targets("에이비씨", None, "123456", us_listed=False), [])


class SectorComparisonTest(unittest.TestCase):
    @staticmethod
    def flow(rows):
        return {"sectors": [
            {"sector_id": sector_id, "name": name, "rates": {"1d": day}, "flow": "횡보"} for sector_id, name, day in rows
        ]}

    def test_groups_by_direction(self):
        cross = compare_sector_flows(
            self.flow([("1", "반도체", -7.0), ("2", "배터리", 1.7), ("3", "원유", 0.5), ("4", "조선", -1.0), ("5", "게임", None), ("6", "종이", 0.2)]),
            self.flow([("1", "반도체", -2.0), ("2", "배터리", -1.9), ("3", "원유", 1.5), ("4", "조선", 2.0), ("5", "게임", 1.0)]),
        )
        self.assertEqual(cross["compared"], 4)  # 게임은 국장 자료 없음, 종이는 미장에 없음
        self.assertEqual(cross["counts"], {"both_up": 1, "both_down": 1, "kr_only": 1, "us_only": 1})
        self.assertEqual(cross["same_direction_ratio"], 0.5)
        self.assertEqual(cross["groups"]["both_down"][0]["name"], "반도체")
        self.assertEqual(cross["groups"]["kr_only"][0]["name"], "배터리")
        self.assertEqual(cross["groups"]["us_only"][0]["name"], "조선")


class TrendTest(unittest.TestCase):
    def test_summary_numbers(self):
        trend = summarize_trend(candles([100, 110, 120, 90, 99]))
        self.assertEqual(trend["change_rate"], -1.0)
        self.assertEqual(trend["max_drawdown"]["rate"], -25.0)
        self.assertEqual(trend["max_drawdown"]["peak_date"], "2026-01-03")
        self.assertEqual(trend["max_drawdown"]["trough_date"], "2026-01-04")
        self.assertEqual((trend["up_days"], trend["down_days"]), (3, 1))
        self.assertEqual(trend["worst_day"]["change_rate"], -25.0)
        self.assertEqual(trend["high"]["price"], 120)

    def test_flow_describes_rise_then_fall(self):
        closes = [100 + step * 2 for step in range(21)] + [140 - step * 3 for step in range(1, 21)]
        trend = summarize_trend(candles(closes[:31]) + [
            {**candle, "date": f"2026-02-{index:02d}"} for index, candle in enumerate(candles(closes[31:]), start=1)
        ])
        self.assertTrue(trend["flow"].startswith("상승"))
        self.assertTrue(trend["flow"].endswith("하락"))

    def test_needs_two_candles(self):
        self.assertIsNone(summarize_trend(candles([100])))
        self.assertIsNone(summarize_trend([]))

    def test_equal_weight_index_uses_common_days(self):
        index = build_equal_weight_index({
            "A": candles([100, 110, 120]),
            "B": candles([50, 50, 40])[:3],
            "C": candles([10]),
        })
        self.assertEqual([row["close"] for row in index], [100.0, 105.0, 100.0])


class TranslationParsingTest(unittest.TestCase):
    def test_joins_segments_and_tolerates_bad_payload(self):
        payload = [[["애플 주가 상승.\n", "Apple rises.\n"], ["수요 부진. ", "Weak demand. "], ["문제인가?", "Trouble?"]], None, "en"]
        self.assertEqual(parse_translation(payload), "애플 주가 상승.\n수요 부진. 문제인가?")
        self.assertEqual(parse_translation(None), "")
        self.assertEqual(parse_translation([]), "")


class ArticleSummaryTest(unittest.TestCase):
    BODY = (
        "A사가 3분기 영업이익이 전년보다 40% 늘었다고 14일 공시했다. "
        "매출은 2조원으로 분기 기준 사상 최대를 기록했다. "
        "회사 측은 반도체 장비 수주가 늘어난 영향이라고 설명했다. "
        "증권가는 4분기에도 수주 증가가 이어질 것으로 내다봤다. "
        "한편 이날 A사 주가는 장 초반 3% 오르다 보합권으로 내려왔다. "
        "무단 전재 및 재배포 금지. "
    ) * 3

    def test_extracts_body_and_skips_boilerplate(self):
        html = f"""<html><head><title>사이트</title><meta property="og:title" content="A사 3분기 영업이익 40% 증가">
            <meta property="og:description" content="짧은 설명"></head><body><nav><p>메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴</p></nav>
            <div itemprop="articleBody"><p>{self.BODY}</p></div></body></html>"""
        article = extract_article(html)
        self.assertEqual(article["title"], "A사 3분기 영업이익 40% 증가")
        summary, source = build_summary(article)
        self.assertEqual(source, "body")
        self.assertIn("영업이익이 전년보다 40% 늘었다", summary)
        self.assertNotIn("무단 전재", summary)
        self.assertNotIn("메뉴", summary)
        self.assertLessEqual(len(summary), 420)

    def test_falls_back_to_description_or_nothing(self):
        description = "B사가 신제품을 공개했다. 회사는 내년 상반기 양산을 목표로 한다고 밝혔다."
        html = f'<html><head><meta name="description" content="{description}"></head><body><p>짧음</p></body></html>'
        self.assertEqual(build_summary(extract_article(html)), (description, "description"))
        self.assertEqual(build_summary(extract_article("<html><body>Please enable JS</body></html>")), ("", "none"))

    def test_json_ld_article_body(self):
        payload = '{"@type": "NewsArticle", "headline": "C사 수주", "articleBody": "' + self.BODY.replace('"', "") + '"}'
        html = f'<html><head><script type="application/ld+json">{payload}</script></head><body></body></html>'
        self.assertEqual(build_summary(extract_article(html))[1], "body")

    def test_sentence_split_keeps_abbreviations_and_decimals(self):
        self.assertEqual(
            split_sentences("The U.S. government would rescue Nvidia Corp. if needed. Shares fell 2.5% on Tuesday."),
            ["The U.S. government would rescue Nvidia Corp. if needed.", "Shares fell 2.5% on Tuesday."],
        )

    def test_language_and_url_guard(self):
        self.assertTrue(is_korean("삼성전자가 실적을 발표했다."))
        self.assertFalse(is_korean("Apple stock rises to a record high."))
        self.assertEqual(google_article_id("https://news.google.com/rss/articles/CBMiabc_DEF-123?oc=5"), "CBMiabc_DEF-123")
        self.assertIsNone(google_article_id("http://127.0.0.1:6379/"))
        self.assertIsNone(google_article_id("https://news.google.com.evil.example/rss/articles/CBMiabc_DEF-123"))


class PriceAdjustmentTest(unittest.TestCase):
    def test_reverse_split_is_reported_with_both_views(self):
        # 15:1 병합: 원주가는 0.138 → 2.0으로 뛰지만 수정주가는 2.07 → 2.0
        adjusted = candles([2.25, 2.07, 2.0, 1.9])
        raw = candles([0.15, 0.138, 2.0, 1.9])
        events = detect_price_adjustments(adjusted, raw)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["date"], event["kind"], event["label"]), ("2026-01-03", "reverse_split", "주식 병합 약 15:1"))
        self.assertEqual(event["raw_change_rate"], 1349.28)
        self.assertEqual(event["adjusted_change_rate"], -3.38)

    def test_split_small_adjustment_and_none(self):
        self.assertEqual(
            detect_price_adjustments(candles([100, 101, 99]), candles([1000, 1010, 99]))[0]["label"], "주식 분할 약 1:10"
        )
        self.assertEqual(
            detect_price_adjustments(candles([83.3, 100]), candles([100, 100]))[0]["kind"], "other"
        )
        self.assertEqual(detect_price_adjustments(candles([100, 110, 90]), candles([100, 110, 90])), [])

    def test_warning_labels(self):
        described = describe_warnings([
            {"warningType": "VI_STATIC", "startDate": "2026-10-13", "endDate": None},
            {"warningType": "VI_DYNAMIC", "startDate": "2026-10-13", "endDate": None},
            {"warningType": "INVESTMENT_WARNING", "startDate": "2026-10-10", "endDate": "2026-10-20"},
        ])
        self.assertEqual([item["label"] for item in described], ["투자경고종목", "변동성 완화장치(VI) 발동"])
        self.assertTrue(described[0]["severe"])


class FlowLabelTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(flow_label({"1d": 1, "1w": 2, "1mo": 3}), "상승 지속")
        self.assertEqual(flow_label({"1d": -1, "1w": 2, "1mo": 3}), "상승 중 단기 조정")
        self.assertEqual(flow_label({"1d": -1, "1w": -2, "1mo": -3}), "하락 지속")
        self.assertEqual(flow_label({"1d": 1, "1w": -2, "1mo": -3}), "하락 중 반등 시도")
        self.assertEqual(flow_label({"1d": 1, "1w": 2, "1mo": -3}), "반등 전환")
        self.assertEqual(flow_label({"1d": 1, "1w": -2, "1mo": 3}), "상승 후 꺾임")
        self.assertEqual(flow_label({"1d": 1, "1w": None, "1mo": 3}), "자료 부족")



class BrowserSummaryEndpointTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.insight_routes import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.body = {"url": "https://news.google.com/rss/articles/CBMiAbCdEfGhIjKl", "page_url": "https://example.com/a", "html": "<p>x</p>"}

    def test_requires_extension_header(self):
        self.assertEqual(self.client.post("/api/v1/article-summary/from-browser", json=self.body).status_code, 403)

    def test_rejects_urls_outside_article_list(self):
        response = self.client.post(
            "/api/v1/article-summary/from-browser",
            json={**self.body, "url": "https://evil.example.com/a"},
            headers={"X-Toss-Extension": "1"},
        )
        self.assertEqual(response.status_code, 422)


class SameStoryTest(unittest.TestCase):
    def test_covers_story_by_title_keywords(self):
        from app.article_summary import covers_story

        title = "Mercer International plans Chapter 11 filing to cut $1.1B debt"
        same = "Mercer International reached a deal with bondholders and will file a prepackaged Chapter 11 plan to cut debt."
        other = "CIBC lowered its rating on Mercer International, citing weaker pulp prices."
        self.assertTrue(covers_story(title, same))
        self.assertFalse(covers_story(title, other))
        self.assertFalse(covers_story("Mercer stock", same))  # 핵심 단어가 너무 적으면 판단하지 않음


class SecFilingTest(unittest.TestCase):
    def test_parse_filing_titles(self):
        from app.sec_filings import parse_filing_title

        self.assertEqual(parse_filing_title("Form 8K MERCER INTERNATIONAL INC. For: 3 November"), ("8-K", "MERCER INTERNATIONAL INC"))
        self.assertEqual(parse_filing_title("Form 10Q Apple Inc For: 27 June"), ("10-Q", "Apple Inc"))
        self.assertEqual(parse_filing_title("Form SC 13G Nvidia Corp"), ("SC 13G", "Nvidia Corp"))
        self.assertIsNone(parse_filing_title("Nvidia shares rise after earnings"))

    def test_company_names_match_sec_titles(self):
        from app.sec_filings import normalize_company

        self.assertEqual(normalize_company("RISING DRAGON ACQUISITION CORP"), normalize_company("Rising Dragon Acquisition Corp."))
        self.assertEqual(normalize_company("MERCER INTERNATIONAL INC"), "MERCER INTERNATIONAL")

    def test_pick_nearest_filing_and_describe(self):
        from datetime import date

        from app.sec_filings import describe_filing, filing_tone, pick_filing

        recent = {
            "form": ["8-K", "10-Q", "8-K"],
            "filingDate": ["2026-11-03", "2026-11-01", "2026-10-29"],
            "accessionNumber": ["a", "b", "c"],
            "primaryDocument": ["a.htm", "b.htm", "c.htm"],
            "items": ["1.02,7.01,9.01", "", "1.01,2.03,9.01"],
        }
        filing = pick_filing(recent, "8-K", date(2026, 11, 3))
        self.assertEqual(filing["accession"], "a")
        self.assertEqual(describe_filing(filing), "SEC 8-K 수시 공시(2026-11-03): 중요 계약 해지 · 투자자 대상 자료 공개(공정공시)")
        self.assertEqual(filing_tone(filing), "악재")
        self.assertIsNone(pick_filing(recent, "8-K", date(2026, 9, 1)))

if __name__ == "__main__":
    unittest.main()
