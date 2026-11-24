"""시장 인사이트 순수 로직 회귀 테스트. 실행: py -m unittest discover -s tests"""
import math
import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("TOSS_CLIENT_ID", "test")
os.environ.setdefault("TOSS_CLIENT_SECRET", "test")

from app.article_summary import build_summary, extract_article, google_article_id, is_korean, split_sentences, summarize_text  # noqa: E402
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

    def test_summary_drops_title_echo_noise_and_questions(self):
        title = "삼성전자 C랩 강점은?…기술 검증 통해 사업화 가능성 점검"
        body = (
            "[뉴스웍스=채윤정 기자] 삼성전자 C랩 강점은 기술 검증을 통해 사업화 가능성을 점검하는 것이다. "
            "박남현 파트장은 10일 기자간담회에서 스타트업 30곳이 삼성전자와 기술 검증을 진행 중이라고 밝혔다. "
            "C랩 아웃사이드의 스타트업 지원 방식은? "
            "삼성전자는 2012년부터 사내벤처와 스타트업 1000곳을 육성해 왔다고 설명했다. "
            "회사는 내년에도 지원 규모를 유지할 계획이라고 덧붙였다. "
        ) * 2
        summary = summarize_text(body, title)
        self.assertNotIn("뉴스웍스", summary)
        self.assertNotIn("지원 방식은?", summary)
        self.assertNotIn("C랩 강점은 기술 검증을 통해", summary)  # 제목 되풀이 문장
        self.assertIn("스타트업 30곳", summary)
        self.assertLessEqual(len(split_sentences(summary)), 2)
        self.assertLessEqual(len(summary), 260)

    def test_description_teaser_and_title_echo_are_filtered(self):
        title = "Windows 11 loses Android phones after standby: what Microsoft is planning"
        article = {
            "title": title,
            "description": "Windows 11 loses the connection to Android phones after standby. "
                           "Want to fix it now? Microsoft says a patch that keeps Phone Link connected will ship in December.",
            "body": "",
        }
        summary, source = build_summary(article)
        self.assertEqual(source, "description")
        self.assertEqual(summary, "Microsoft says a patch that keeps Phone Link connected will ship in December.")

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

    FILLER = " Analysts were watching the sector closely." * 6  # 본문 길이 기준(200자)을 채우는 무관한 문장

    def test_same_story_rejects_title_lookalikes(self):
        from datetime import date

        from app.article_summary import same_story

        title = "Mercer International Q3 revenue falls 12% on weak pulp prices"
        body = "Mercer International said Q3 revenue fell 12% as weak pulp prices hit sales in Europe." + self.FILLER
        self.assertTrue(same_story(title, {"body": body, "published": "2026-11-03T13:00:00Z"}, date(2026, 11, 3)))
        # 제목은 거의 같지만 지난 분기 기사: 숫자가 다르고 게시일도 멀다.
        old = "Mercer International said Q2 revenue fell 8% as weak pulp prices hit sales in Europe." + self.FILLER
        self.assertFalse(same_story(title, {"body": old}, date(2026, 11, 3)))
        self.assertFalse(same_story(title, {"body": body, "published": "2026-08-01"}, date(2026, 11, 3)))
        # 며칠 뒤의 후속 보도(또는 페이지 생성 시각)는 받는다.
        self.assertTrue(same_story(title, {"body": body, "published": "2026-11-10"}, date(2026, 11, 3)))
        # 게시일을 모르면 본문만으로 판단한다.
        self.assertTrue(same_story(title, {"body": body}, date(2026, 11, 3)))

    def test_same_story_rejects_quote_pages(self):
        from app.article_summary import same_story

        title = "Mercer International Stock Price Forecast. Should You Buy MERC?"
        quote_page = {
            "title": "Mercer International Inc. (MERC) Stock Price, News, Quote & History",
            "description": "Find the latest Mercer International Inc. (MERC) stock quote, history, news and price you need.",
            "body": "",
        }
        self.assertFalse(same_story(title, quote_page))

    def test_same_story_korean_titles(self):
        from app.article_summary import same_story

        title = "삼성전자, 美 테일러 공장 2나노 수주 확대"
        body = "삼성전자는 미국 테일러 공장에서 2나노 공정 수주를 늘리고 있다고 밝혔다." + self.FILLER
        other = "SK하이닉스는 청주 공장 증설 계획을 발표했다." + self.FILLER
        self.assertTrue(same_story(title, {"body": body}))
        self.assertFalse(same_story(title, {"body": other}))

    def test_extract_published_date(self):
        html = '<html><head><meta property="article:published_time" content="2026-11-03T09:00:00Z"></head><body></body></html>'
        self.assertEqual(extract_article(html)["published"], "2026-11-03T09:00:00Z")
        ld = '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-02"}</script>'
        self.assertEqual(extract_article(f"<html><head>{ld}</head><body></body></html>")["published"], "2026-11-02")


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


class FastNewsTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_client_skips_when_all_models_cooling_down(self):
        import time
        from unittest import mock

        from app import ai_client

        settings = mock.Mock(ai_providers=[("http://ai.invalid", "k", ["m1", "m2"])])
        later = time.monotonic() + 60
        with mock.patch.object(ai_client, "get_settings", return_value=settings), \
                mock.patch.dict(ai_client._unavailable_until, {"http://ai.invalid|m1": later, "http://ai.invalid|m2": later}):
            with self.assertRaises(ai_client.AIUnavailableError):
                await ai_client.chat_completion([{"role": "user", "content": "hi"}])

    async def test_ai_client_falls_through_to_second_provider(self):
        from unittest import mock

        import httpx

        from app import ai_client

        calls = []

        def handler(request):
            calls.append((request.url.host, request.headers["Authorization"]))
            if request.url.host == "first.invalid":
                return httpx.Response(429, json={})
            return httpx.Response(200, json={"choices": [{"message": {"content": " 요약 "}}]})

        real_client = httpx.AsyncClient
        settings = mock.Mock(ai_providers=[
            ("https://first.invalid/v1", "key1", ["gemini"]), ("https://second.invalid/v1", "key2", ["llama"]),
        ])
        with mock.patch.object(ai_client, "get_settings", return_value=settings), \
                mock.patch.dict(ai_client._unavailable_until, {}, clear=True), \
                mock.patch.object(ai_client.httpx, "AsyncClient",
                                  side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
            answer = await ai_client.chat_completion([{"role": "user", "content": "hi"}])
            self.assertIn("https://first.invalid/v1|gemini", ai_client._unavailable_until)
        self.assertEqual(answer, "요약")
        self.assertEqual(calls, [("first.invalid", "Bearer key1"), ("second.invalid", "Bearer key2")])

    async def test_staged_news_returns_rule_result_then_upgrades(self):
        from unittest import mock

        from app.api import insight_routes

        store = {}

        async def get_json(key):
            return store.get(key)

        async def set_json(key, value, ttl):
            store[key] = {"value": value, "ttl": ttl}

        async def build_fast():
            return {"articles": [{"tone": "중립"}]}

        async def upgrade(result):
            result["articles"] = [{"tone": "호재"}]
            return result

        with mock.patch.object(insight_routes, "get_json", new=get_json), \
                mock.patch.object(insight_routes, "set_json", new=set_json), \
                mock.patch.object(insight_routes, "ai_enabled", return_value=True):
            first = await insight_routes.staged_news_json("k", 600, build_fast, upgrade)
            self.assertTrue(first["ai_pending"])
            self.assertEqual(store["k"]["ttl"], insight_routes.AI_PENDING_TTL)
            await insight_routes._ai_upgrades["k"]
        self.assertEqual(store["k"]["value"]["articles"], [{"tone": "호재"}])
        self.assertFalse(store["k"]["value"]["ai_pending"])
        self.assertEqual(store["k"]["ttl"], 600)

if __name__ == "__main__":
    unittest.main()


class RecommendationTest(unittest.TestCase):
    def sector(self, sector_id, flow, week, month, quarter, amount=1.0):
        return {"sector_id": sector_id, "name": sector_id, "flow": flow, "trading_amount": amount,
                "rates": {"1d": 0.5, "1w": week, "1mo": month, "3mo": quarter}}

    def test_themes_split_into_trend_and_rebound(self):
        from app.recommendations import pick_themes, score_sectors

        sectors = [
            self.sector("strong", "상승 지속", 5, 15, 20),
            self.sector("mild", "상승 지속", 1, 3, 2),
            self.sector("thin", "상승 지속", 5, 15, 20, amount=None),
            self.sector("bounce", "반등 전환", 6, -5, -10),
            self.sector("weak", "하락 지속", -3, -8, -12),
        ]
        trend, rebound, risk = pick_themes(score_sectors(sectors))
        self.assertEqual([sector["sector_id"] for sector in trend][:2], ["strong", "thin"])
        self.assertTrue(trend[1]["thin"])  # 거래대금 정보가 없으면 감점되어 뒤로
        self.assertEqual([sector["sector_id"] for sector in rebound], ["bounce"])
        self.assertNotIn("weak", [sector["sector_id"] for sector in trend + rebound])
        self.assertEqual([sector["sector_id"] for sector in risk], ["weak"])

    def test_risk_stocks_rank_weak_volatile_and_warned_first(self):
        from app.recommendations import risk_stock_reasons, score_stocks

        def snap(symbol, r20, above=True, volatility=2.0, warnings=()):
            return {"symbol": symbol, "rates": {"5d": 0, "20d": r20, "60d": r20}, "trading_amount": 1e9,
                    "above_ma20": above, "volatility": volatility, "max_drawdown": -5.0, "warnings": list(warnings)}

        severe = [{"label": "투자경고", "severe": True}]
        ranked = score_stocks([snap("OK", 10), snap("WEAK", -15, above=False, volatility=6.0), snap("WARN", 5, warnings=severe)], "risk")
        self.assertEqual([stock["symbol"] for stock in ranked], ["WEAK", "WARN", "OK"])  # 위험 종목은 유의사항 종목도 빼지 않음
        reasons, _ = risk_stock_reasons(ranked[0], {"rates": {"1mo": -3.0}})
        self.assertTrue(any("20일 평균선 아래" in reason for reason in reasons))
        self.assertTrue(any("분야 20일" in reason for reason in reasons))

    def test_stocks_with_severe_warnings_are_excluded_and_duplicates_assigned_once(self):
        from app.recommendations import assign_stocks, score_stocks

        def snap(symbol, r20, severe=False):
            return {"symbol": symbol, "rates": {"5d": 1, "20d": r20, "60d": r20}, "trading_amount": 1e9,
                    "above_ma20": True, "volatility": 2.0, "max_drawdown": -5.0,
                    "warnings": [{"label": "투자경고", "severe": True}] if severe else []}

        scored = score_stocks([snap("A", 10), snap("B", 30, severe=True), snap("C", 5)], "trend")
        self.assertEqual([stock["symbol"] for stock in scored], ["A", "C"])

        themes = [{"ranked": [{"symbol": "X"}, {"symbol": "Y"}]}, {"ranked": [{"symbol": "X"}, {"symbol": "Z"}]}]
        assign_stocks(themes)
        self.assertEqual([stock["symbol"] for stock in themes[0]["stocks"]], ["X", "Y"])
        self.assertEqual([stock["symbol"] for stock in themes[1]["stocks"]], ["Z"])


class AiRecommendTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_picks_are_limited_to_candidate_pool(self):
        import json
        from unittest import mock

        from app import recommendations

        theme = {"kind": "trend", "name": "여행", "sector_id": "7", "flow": "상승 지속", "rates": {}, "reasons": [], "risks": [], "news": None}
        stock = {"symbol": "A1", "name": "에이", "currency": "KRW", "close": 1000, "rates": {"5d": 1, "20d": 5, "60d": 9},
                 "above_ma20": True, "max_drawdown": -5, "volatility": 2, "warnings": [], "score": 0.8}
        pool = [((stock, theme), recommendations._pool_row(stock, theme))]
        risk_theme = {**theme, "kind": "risk", "name": "조선", "sector_id": "9", "flow": "하락 지속"}
        risk_stock = {**stock, "symbol": "R1", "name": "알"}
        risk_pool = [((risk_stock, risk_theme), recommendations._pool_row(risk_stock, risk_theme))]
        answer = json.dumps({"summary": "요약", "picks": [
            {"symbol": "ZZZ", "reason": "목록 밖", "risk": ""},
            {"symbol": "A1", "reason": "20일 +5%", "risk": "변동성"},
            {"symbol": "A1", "reason": "중복", "risk": ""},
            {"symbol": "R1", "reason": "위험 목록 종목은 추천에 못 들어감", "risk": ""},
        ], "risk_picks": [{"symbol": "A1", "reason": "추천 목록 종목은 위험에 못 들어감"}, {"symbol": "R1", "reason": "20일 평균선 아래"}]})
        with mock.patch.object(recommendations, "chat_completion", new=mock.AsyncMock(return_value=answer)):
            summary, picks, risk_picks = await recommendations.ai_recommend("KR", [theme, risk_theme], pool, risk_pool)
        self.assertEqual(summary, "요약")
        self.assertEqual([pick["symbol"] for pick in picks], ["A1"])
        self.assertEqual(picks[0]["theme"], "여행")
        self.assertEqual([pick["symbol"] for pick in risk_picks], ["R1"])
        self.assertEqual(risk_picks[0]["theme"], "조선")


class RetryAfterTest(unittest.TestCase):
    def test_short_rate_limits_use_retry_after(self):
        import httpx

        from app.ai_client import COOLDOWN_SECONDS, _retry_after

        self.assertEqual(_retry_after(httpx.Response(429, headers={"retry-after": "7"})), 7.0)
        self.assertEqual(_retry_after(httpx.Response(429, headers={"retry-after": "9999"})), COOLDOWN_SECONDS)
        self.assertEqual(_retry_after(httpx.Response(503)), COOLDOWN_SECONDS)


class PriceEventTest(unittest.TestCase):
    def test_split_like_day_lowers_risk_and_adds_note(self):
        from app.recommendations import price_event_note, score_stocks

        def snap(symbol, event=None):
            return {"symbol": symbol, "rates": {"5d": 0, "20d": -10, "60d": -80}, "trading_amount": 1e9,
                    "above_ma20": False, "volatility": 9.0, "max_drawdown": -85.0, "warnings": [], "price_event": event}

        spun_off = snap("SPIN", {"date": "2026-10-01", "change_rate": -83.8})
        ranked = score_stocks([spun_off, snap("REAL")], "risk")
        self.assertEqual([stock["symbol"] for stock in ranked], ["REAL", "SPIN"])
        self.assertIn("권리 변동", price_event_note(spun_off))
        self.assertIsNone(price_event_note(snap("REAL")))


class AccountOrderTest(unittest.IsolatedAsyncioTestCase):
    def test_order_input_validation(self):
        from app.account import AccountError, validate_order_input

        body = validate_order_input({"symbol": "005930", "side": "buy", "order_type": "limit", "quantity": 3, "price": 70000}, "KRW")
        self.assertEqual(body, {"symbol": "005930", "side": "BUY", "orderType": "LIMIT", "timeInForce": "DAY", "quantity": 3, "price": 70000})
        with self.assertRaises(AccountError):  # 국내 소수 수량
            validate_order_input({"symbol": "005930", "side": "BUY", "order_type": "MARKET", "quantity": 1.5}, "KRW")
        with self.assertRaises(AccountError):  # 지정가인데 가격 없음
            validate_order_input({"symbol": "AAPL", "side": "BUY", "order_type": "LIMIT", "quantity": 1}, "USD")
        with self.assertRaises(AccountError):  # 국내 가격은 정수
            validate_order_input({"symbol": "005930", "side": "BUY", "order_type": "LIMIT", "quantity": 1, "price": 70000.5}, "KRW")
        us = validate_order_input({"symbol": "AAPL", "side": "SELL", "order_type": "MARKET", "quantity": 0.5}, "USD")
        self.assertEqual(us["quantity"], 0.5)
        self.assertNotIn("price", us)

    def test_order_amount_cap(self):
        from unittest import mock

        from app import account

        settings = mock.Mock(max_order_amount_krw=1_000_000, max_order_amount_usd=1000.0)
        with mock.patch.object(account, "get_settings", return_value=settings):
            self.assertIsNone(account.order_limit_problem(900_000, "KRW"))
            self.assertIn("상한", account.order_limit_problem(1_500_000, "KRW"))
            self.assertIn("상한", account.order_limit_problem(1500, "USD"))
            self.assertIsNotNone(account.order_limit_problem(None, "KRW"))

    async def test_confirm_uses_token_once_and_as_idempotency_key(self):
        import json
        from unittest import mock

        from app import account

        token = "12345678-1234-1234-1234-123456789abc"
        saved = {"body": {"symbol": "005930", "side": "BUY", "orderType": "LIMIT", "timeInForce": "DAY", "quantity": 1, "price": 70000},
                 "currency": "KRW", "estimated": 70000, "name": "삼성전자", "username": "alice"}
        other = "87654321-4321-4321-4321-cba987654321"
        store = {f"account:order-confirm:{token}": json.dumps(saved), f"account:order-confirm:{other}": json.dumps(saved)}

        async def getdel(key):
            return store.pop(key, None)

        client = mock.Mock()
        client.create_order = mock.AsyncMock(return_value={"orderId": "ORD1"})
        settings = mock.Mock(trading_enabled=True, max_order_amount_krw=1_000_000, max_order_amount_usd=1000.0)
        with mock.patch.object(account, "get_settings", return_value=settings), mock.patch.object(account.redis, "getdel", new=getdel):
            with self.assertRaises(account.AccountError) as raised:  # 사용자가 '실제 주문 허용'을 안 켰으면 거절
                await account.confirm_order(client, token, "alice", user_trading=False)
            self.assertEqual(raised.exception.code, "user-trading-disabled")
            with self.assertRaises(account.AccountError) as raised:  # 다른 사용자가 만든 토큰은 거절
                await account.confirm_order(client, other, "mallory", user_trading=True)
            self.assertEqual(raised.exception.code, "confirm-owner-mismatch")
            result = await account.confirm_order(client, token, "alice", user_trading=True)
            self.assertEqual(result["order_id"], "ORD1")
            self.assertEqual(client.create_order.call_args.args[0]["clientOrderId"], token)
            with self.assertRaises(account.AccountError):  # 두 번째 사용은 거절
                await account.confirm_order(client, token, "alice", user_trading=True)
        self.assertEqual(client.create_order.call_count, 1)

    async def test_confirm_refused_when_trading_disabled(self):
        from unittest import mock

        from app import account

        client = mock.Mock()
        client.create_order = mock.AsyncMock()
        with mock.patch.object(account, "get_settings", return_value=mock.Mock(trading_enabled=False)):
            with self.assertRaises(account.AccountError) as raised:
                await account.confirm_order(client, "12345678-1234-1234-1234-123456789abc")
        self.assertEqual(raised.exception.code, "trading-disabled")
        client.create_order.assert_not_called()

    def test_holdings_normalized_and_account_masked(self):
        from app.account import mask_account_no, normalize_holdings

        raw = {"profitLoss": {"amount": {"krw": "1000", "usd": "0.7"}, "rate": "0.025"},
               "items": [{"symbol": "005930", "name": "삼성전자", "currency": "KRW", "quantity": "3", "lastPrice": "71000",
                          "averagePurchasePrice": "70000", "marketValue": {"amount": "213000", "purchaseAmount": "210000"},
                          "profitLoss": {"amount": "3000", "rate": "0.0143"}, "dailyProfitLoss": {"amount": "500", "rate": "0.002"}}]}
        holdings = normalize_holdings(raw)
        self.assertEqual(holdings["items"][0]["quantity"], 3.0)
        self.assertEqual(holdings["items"][0]["profit_rate"], 1.43)  # 토스 rate는 비율(0.0143) → %
        self.assertEqual(holdings["profit_rate"], 2.5)
        self.assertEqual(holdings["profit_loss"]["krw"], 1000.0)
        self.assertEqual(mask_account_no("10212345678"), "102******78")


class AccountAccessTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.account_routes import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_rejects_non_local_host_and_foreign_origin(self):
        self.assertEqual(self.client.get("/api/v1/watchlist", headers={"host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/watchlist", headers={"host": "127.0.0.1:9000", "origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/watchlist", headers={"host": "127.0.0.1:9000", "sec-fetch-site": "cross-site"}).status_code, 403)

    def test_order_endpoints_need_custom_header(self):
        response = self.client.post("/api/v1/account/orders/confirm", json={"confirm_token": "x"}, headers={"host": "127.0.0.1:9000"})
        self.assertEqual(response.status_code, 403)


class StockStatusLineTest(unittest.TestCase):
    def test_flags_price_source_mismatch_for_holdings(self):
        from app.account import status_lines

        snapshot = {"day_change": 3.4, "close": 1.95, "rates": {"20d": -40.5, "60d": -85.0}, "above_ma20": False,
                    "volatility": 8.2, "max_drawdown": -86.5, "warnings": [], "price_event": None}
        texts = [line["text"] for line in status_lines(snapshot, {"quantity": 8, "average_price": 22.48, "profit_rate": -91.32, "last_price": 22.28}, None)]
        self.assertTrue(any("보유 현재가" in text for text in texts))  # 시세 자료와 보유 현재가가 크게 다를 때
        texts = [line["text"] for line in status_lines(snapshot, {"quantity": 8, "average_price": 22.48, "profit_rate": -91.32, "last_price": 1.95}, None)]
        self.assertFalse(any("보유 현재가" in text for text in texts))
        self.assertTrue(any("수익률 -91.32%" in text for text in texts))


class FakeRedis:
    """users 모듈이 쓰는 Redis 명령만 흉내 냅니다(만료는 무시)."""

    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def expire(self, key, seconds):
        return True


class UserAuthTest(unittest.IsolatedAsyncioTestCase):
    def test_password_hash_roundtrip(self):
        from app.users import hash_password, verify_password

        stored = hash_password("correct horse")
        self.assertNotIn("correct horse", stored)
        self.assertTrue(verify_password("correct horse", stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_secret_encryption_needs_server_key(self):
        from unittest import mock

        from app import users

        with mock.patch.object(users, "get_settings", return_value=mock.Mock(app_secret_key="x" * 40)):
            token = users.encrypt_secret("my-client-secret")
            self.assertNotIn("my-client-secret", token)
            self.assertEqual(users.decrypt_secret(token), "my-client-secret")
        with mock.patch.object(users, "get_settings", return_value=mock.Mock(app_secret_key="y" * 40)):
            with self.assertRaises(users.UserError):  # 서버 비밀값이 바뀌면 풀 수 없음
                users.decrypt_secret(token)
        with mock.patch.object(users, "get_settings", return_value=mock.Mock(app_secret_key="")):
            with self.assertRaises(users.UserError) as raised:
                users.encrypt_secret("s")
            self.assertEqual(raised.exception.status_code, 503)

    async def test_signup_login_lockout_and_sessions(self):
        from unittest import mock

        from app import users

        fake = FakeRedis()
        settings = mock.Mock(signup_enabled=True, signup_invite_code="club", trading_enabled=False)
        with mock.patch.object(users, "redis", fake), mock.patch.object(users, "get_settings", return_value=settings):
            with self.assertRaises(users.UserError):  # 초대 코드 틀림
                await users.signup("alice", "password123", "nope")
            await users.signup("Alice", "password123", "club")
            with self.assertRaises(users.UserError):  # 같은 아이디(대소문자 무시)
                await users.signup("alice", "password123", "club")
            token, user = await users.login("alice", "password123", "1.2.3.4")
            self.assertEqual((await users.session_user(token))["username"], "alice")
            self.assertFalse(any(token in key for key in fake.data))  # 세션 토큰 원문은 저장하지 않음
            self.assertNotIn("password", users.public_user(user))
            await users.logout(token)
            self.assertIsNone(await users.session_user(token))
            for _ in range(users.LOGIN_FAIL_LIMIT_USER):
                with self.assertRaises(users.UserError):
                    await users.login("alice", "bad-password", "1.2.3.4")
            with self.assertRaises(users.UserError) as raised:  # 실패가 쌓이면 맞는 비밀번호도 잠김
                await users.login("alice", "password123", "1.2.3.4")
            self.assertEqual(raised.exception.status_code, 429)


class AccountLoginRequiredTest(unittest.TestCase):
    def test_account_endpoints_need_login(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.account_routes import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        local = {"host": "127.0.0.1:9000"}
        self.assertEqual(client.get("/api/v1/account/summary", headers=local).status_code, 401)
        self.assertEqual(client.get("/api/v1/account/summary", headers={"host": "evil.example"}).status_code, 403)
        self.assertEqual(client.post("/api/v1/auth/login", json={"username": "a", "password": "b"}, headers=local).status_code, 403)  # 헤더 없음


class PriceCurrencyTest(unittest.TestCase):
    def test_krw_price_converted_to_usd_with_tick_rounding(self):
        from app.account import AccountError, convert_krw_price, price_tick

        fx = {"mid": 1347.44}
        self.assertEqual(convert_krw_price(5390, fx), 4.0)  # 5,390원 ÷ 1,347.44 = $4.0002 → $4.00
        self.assertEqual(convert_krw_price(1000, fx), 0.7421)  # $1 미만은 소수 넷째 자리
        self.assertEqual(price_tick(123.456), 123.46)
        with self.assertRaises(AccountError):  # 환율이 없으면 바꾸지 않음
            convert_krw_price(5390, None)


class StockSearchTest(unittest.TestCase):
    def test_search_by_name_and_code(self):
        from app.stock_master import _normalize, search_master

        rows = [
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "type": "STOCK", "common": True},
            {"symbol": "005935", "name": "삼성전자우", "market": "KOSPI", "type": "STOCK", "common": False},
            {"symbol": "028260", "name": "삼성물산", "market": "KOSPI", "type": "STOCK", "common": True},
            {"symbol": "NVDA", "name": "엔비디아", "market": "NASDAQ", "type": "STOCK", "common": True},
            {"symbol": "NVDL", "name": "그래닛셰어즈 엔비디아 2배 ETF", "market": "NASDAQ", "type": "ETF", "common": True},
        ]
        master = [{**row, "key": _normalize(row["name"]), "symbol_key": row["symbol"].lower()} for row in rows]
        self.assertEqual([row["symbol"] for row in search_master(master, "삼성전자")], ["005930", "005935"])
        self.assertEqual([row["symbol"] for row in search_master(master, "삼성")], ["005930", "028260", "005935"])  # 보통주 먼저, 우선주는 뒤
        self.assertEqual([row["symbol"] for row in search_master(master, "엔비디아")][:2], ["NVDA", "NVDL"])
        self.assertEqual(search_master(master, "nvda")[0]["symbol"], "NVDA")
        self.assertEqual(search_master(master, "005930")[0]["name"], "삼성전자")
        self.assertEqual(search_master(master, "  "), [])


def _candles(closes, start=date(2025, 1, 1), volumes=None):
    rows = []
    for index, close in enumerate(closes):
        rows.append({"date": (start + timedelta(days=index)).isoformat(), "close": close,
                     "volume": (volumes[index] if volumes else 1000.0)})
    return rows


class SignalsTest(unittest.TestCase):
    def test_uptrend_scores_high_and_downtrend_low(self):
        from app.signals import compute_signals

        up = compute_signals(_candles([100 + index * 0.5 for index in range(260)]), warnings=[])
        down = compute_signals(_candles([300 - index * 0.8 for index in range(260)]), warnings=[])
        self.assertGreaterEqual(up["score"], 55)
        self.assertLessEqual(down["score"], 45)
        self.assertGreater(up["score"] - down["score"], 20)
        self.assertTrue(any("정배열" in text for text in up["positives"]))
        self.assertTrue(any("역배열" in text for text in down["negatives"]))

    def test_rsi_flows_and_warnings(self):
        from app.signals import flow_factor, rsi, warning_factor

        self.assertEqual(rsi([float(value) for value in range(1, 30)]), 100.0)
        records = [{"foreigner": {"netBuyVolume": "100"}, "institution": {"netBuyVolume": "50"}}] * 5
        self.assertEqual(flow_factor(records)["signal"], 1)
        records = [{"foreigner": {"netBuyVolume": "-100"}, "institution": {"netBuyVolume": "-50"}}] * 5
        self.assertEqual(flow_factor(records)["signal"], -1)
        self.assertEqual(warning_factor([{"label": "투자경고"}])["signal"], -1)
        self.assertIsNone(flow_factor([]))

    def test_volume_confirmation(self):
        from app.signals import technical_factors

        closes = [100.0] * 55 + [100 + index * 2 for index in range(1, 6)]
        volumes = [1000.0] * 55 + [3000.0] * 5
        volume = next(factor for factor in technical_factors(_candles(closes, volumes=volumes)) if factor["key"] == "volume")
        self.assertEqual(volume["label"], "거래량 동반 상승")
        self.assertEqual(volume["signal"], 0)  # 11-17 검증: 이후 수익이 평균보다 낮아 점수에는 넣지 않음
        falling = [100.0] * 55 + [100 - index * 2 for index in range(1, 6)]
        volume = next(factor for factor in technical_factors(_candles(falling, volumes=volumes)) if factor["key"] == "volume")
        self.assertEqual(volume["signal"], -1)

    def test_us_pressure_is_reference_only(self):
        from app.signals import compute_signals, pressure_factor

        closes = [100.0 + (index % 2) * 0.5 for index in range(40)] + [101 + index for index in range(20)]
        volumes = [1000.0] * 60
        buying = pressure_factor(_candles(closes, volumes=volumes))
        self.assertEqual((buying["signal"], buying["weight"], buying["reference"]), (1, 0.0, True))
        falling = pressure_factor(_candles([200.0 - index for index in range(60)], volumes=volumes))
        self.assertEqual(falling["signal"], -1)
        plain = compute_signals(_candles(closes, volumes=volumes))
        with_pressure = compute_signals(_candles(closes, volumes=volumes), pressure=True)
        self.assertEqual(plain["score"], with_pressure["score"])  # 점수에는 영향 없음
        self.assertIn("pressure", [factor["key"] for factor in with_pressure["factors"]])
        self.assertFalse(any("압력" in text for text in with_pressure["positives"]))  # AI 입력용 긍정 목록에도 넣지 않음
        self.assertIsNone(pressure_factor(_candles([100.0] * 10)))

    def test_market_is_weak(self):
        from app.simulation import market_is_weak

        self.assertIsNone(market_is_weak([100.0] * 30))
        self.assertTrue(market_is_weak([100.0] * 59 + [90.0]))
        self.assertFalse(market_is_weak([100.0] * 59 + [110.0]))

    def test_weak_market_halves_short_backtest(self):
        from app.simulation import MIN_HISTORY, run_backtest

        length = MIN_HISTORY + 45
        series = {f"UP{n}": _candles([100 + index * (0.3 + n * 0.05) for index in range(length)]) for n in range(16)}
        falling_index = _candles([1000 - index for index in range(length)])
        normal = run_backtest(series, {}, 5, 5, None, falling_index, True, "inverse_vol")
        halved = run_backtest(series, {}, 5, 5, None, falling_index, True, "inverse_vol", None, True)
        self.assertTrue(all(period["weak_market"] for period in halved["periods"]))
        self.assertAlmostEqual(halved["summary"]["average_invested"], normal["summary"]["average_invested"] / 2, places=3)


class BacktestTest(unittest.TestCase):
    def test_backtest_uses_only_past_data(self):
        from app.simulation import MIN_HISTORY, run_backtest

        length = MIN_HISTORY + 60
        series = {}
        # 꾸준히 오르는 종목(점수 높음)과 꾸준히 내리는 종목(점수 낮음). LOSER는 마지막 구간에만 폭등합니다.
        for number in range(6):
            series[f"UP{number}"] = _candles([100 + index * (0.3 + number * 0.05) for index in range(length)])
        for number in range(6):
            closes = [200 - index * (0.3 + number * 0.05) for index in range(length)]
            series[f"DN{number}"] = _candles(closes)
        loser = [200 - index * 0.6 for index in range(length)]
        loser[-20:] = [value * 3 for value in loser[-20:]]
        series["LOSER"] = _candles(loser)
        result = run_backtest(series, {}, horizon=20, top=3)
        self.assertGreater(result["summary"]["period_count"], 0)
        for period in result["periods"]:
            self.assertTrue(all(pick["symbol"].startswith("UP") for pick in period["picks"]))  # 미래 폭등을 보고 고르지 않음
        self.assertTrue(all(row["symbol"] != "LOSER" for period in result["periods"] for row in period["picks"]))
        self.assertIn("hit_rate", result["summary"])
        self.assertIn("high", result["summary"]["score_buckets"])


class RateLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_limit_and_logged_in_bonus(self):
        from unittest import mock

        from fastapi import HTTPException

        from app import rate_limit

        fake = FakeRedis()
        request = mock.Mock()
        settings = mock.Mock(rate_limit_enabled=True)
        limit = rate_limit.LIMITS["analysis"]
        with mock.patch.object(rate_limit, "redis", fake), mock.patch.object(rate_limit, "get_settings", return_value=settings):
            with mock.patch.object(rate_limit, "_identity", new=mock.AsyncMock(return_value=("ip:1.1.1.1", False))):
                for _ in range(limit.anonymous):
                    await rate_limit.enforce(request, "analysis")
                with self.assertRaises(HTTPException) as raised:
                    await rate_limit.enforce(request, "analysis")
                self.assertEqual(raised.exception.status_code, 429)
                self.assertIn("로그인", raised.exception.detail)
                self.assertIn("Retry-After", raised.exception.headers)
            with mock.patch.object(rate_limit, "_identity", new=mock.AsyncMock(return_value=("user:alice", True))):
                for _ in range(limit.user):
                    await rate_limit.enforce(request, "analysis")  # 로그인 사용자는 따로 더 큰 한도
        with mock.patch.object(rate_limit, "get_settings", return_value=mock.Mock(rate_limit_enabled=False)):
            await rate_limit.enforce(request, "analysis")  # 꺼 두면 세지 않음


class VirtualInvestTest(unittest.TestCase):
    def test_next_tuesday(self):
        from app.virtual import next_tuesday

        self.assertEqual(next_tuesday(date(2026, 11, 15)), date(2026, 11, 17))  # 일요일 → 이틀 뒤 화요일
        self.assertEqual(next_tuesday(date(2026, 11, 17)), date(2026, 11, 24))  # 화요일 → 다음 주 화요일
        self.assertEqual(next_tuesday(date(2026, 11, 18)), date(2026, 11, 24))

    def test_portfolio_buys_whole_shares_and_tracks_money(self):
        from app.simulation import MIN_HISTORY
        from app.virtual import run_portfolio

        length = MIN_HISTORY + 45
        series = {f"UP{n}": _candles([100 + index * (0.3 + n * 0.05) for index in range(length)]) for n in range(6)}
        series.update({f"DN{n}": _candles([200 - index * (0.3 + n * 0.05) for index in range(length)]) for n in range(6)})
        result = run_portfolio(series, {}, 1_000_000, horizon=20, top=3)
        self.assertGreater(result["period_count"], 0)
        for period in result["periods"]:
            self.assertTrue(all(isinstance(trade["shares"], int) and trade["shares"] > 0 for trade in period["trades"]))
            self.assertTrue(all(trade["symbol"].startswith("UP") for trade in period["trades"]))
            self.assertGreaterEqual(period["cash_left"], 0)
        self.assertGreater(result["final_value"], 1_000_000)  # 오르는 종목만 샀으니 이익
        self.assertEqual(result["equity"][0]["value"], 1_000_000)

    def test_forward_portfolio_buys_next_open_and_compares_index(self):
        from app.virtual import evaluate

        portfolio = {"id": "abc", "market": "KR", "currency": "KRW", "created_date": "2026-11-15", "end_date": "2026-11-17",
                     "capital": 1_000_000, "picks": [{"symbol": "A", "name": "에이"}, {"symbol": "B", "name": "비"}]}
        prices = {
            "A": [{"date": "2026-11-13", "open": 90, "close": 95}, {"date": "2026-11-16", "open": 100, "close": 110},
                  {"date": "2026-11-17", "open": 110, "close": 120}, {"date": "2026-11-18", "open": 1, "close": 1}],
            "B": [{"date": "2026-11-16", "open": 1000, "close": 900}, {"date": "2026-11-17", "open": 900, "close": 950}],
        }
        index = [{"date": "2026-11-16", "open": 2000, "close": 2010}, {"date": "2026-11-17", "open": 2010, "close": 2020}]
        result = evaluate(portfolio, prices, index, "2026-11-18")
        holdings = {holding["symbol"]: holding for holding in result["holdings"]}
        self.assertEqual(holdings["A"]["buy_price"], 100)  # 만든 날(11-15) 다음 거래일 시가
        self.assertEqual(holdings["A"]["last_price"], 120)  # 종료일(11-17) 종가, 11-18은 무시
        self.assertEqual(holdings["A"]["shares"], 4992)  # 50만원 / (100 × 1.0015) → 정수 주
        self.assertEqual(result["status"], "finished")
        self.assertAlmostEqual(result["index_return"], 0.01, places=3)
        pending = evaluate({**portfolio, "created_date": "2026-11-17"}, {"A": [], "B": []}, [], "2026-11-17")
        self.assertEqual(pending["status"], "pending")


class CombinedPickTest(unittest.TestCase):
    def test_combined_puts_overlap_first_then_ai_then_theme(self):
        from app.virtual import pick_recommendations

        def stock(symbol, score):
            return {"symbol": symbol, "name": symbol, "evidence": {"score": score}}

        result = {
            "ai_picks": [stock("AI1", 50), stock("BOTH", 60), stock("AI2", 55)],
            "themes": [
                {"kind": "trend", "name": "여행", "stocks": [stock("T1", 80), stock("BOTH", 60), stock("T2", 70)]},
                {"kind": "risk", "name": "음식료", "stocks": [stock("RISK", 99)]},
            ],
        }
        picks = pick_recommendations(result, "all")
        self.assertEqual([pick["symbol"] for pick in picks], ["BOTH", "AI1", "AI2", "T1", "T2"])
        self.assertEqual(picks[0]["via"], ["ai", "theme"])
        self.assertEqual(picks[3]["theme"], "여행")
        self.assertNotIn("RISK", [pick["symbol"] for pick in pick_recommendations(result, "theme")])  # 위험 테마는 제외
        self.assertEqual([pick["symbol"] for pick in pick_recommendations(result, "ai")], ["AI1", "BOTH", "AI2"])


class CombinedRecommendationTest(unittest.TestCase):
    def test_combined_keeps_details_and_sources(self):
        from app.recommendations import combined_picks, with_combined

        def stock(symbol, score, **extra):
            return {"symbol": symbol, "name": symbol, "evidence": {"score": score}, **extra}

        themes = [{"kind": "trend", "name": "여행", "sector_id": "7", "stocks": [stock("T1", 80, reasons=["20일 +10%"]), stock("BOTH", 60, reasons=["규칙 근거"])]},
                  {"kind": "risk", "name": "음식료", "sector_id": "9", "stocks": [stock("RISK", 99)]}]
        ai = [stock("AI1", 50, reason="AI 이유"), stock("BOTH", 60, reason="AI가 본 이유")]
        picks = combined_picks(themes, ai)
        self.assertEqual([pick["symbol"] for pick in picks], ["BOTH", "AI1", "T1"])
        both = picks[0]
        self.assertEqual(both["via"], ["ai", "theme"])
        self.assertEqual(both["reason"], "AI가 본 이유")  # AI 설명과
        self.assertEqual(both["reasons"], ["규칙 근거"])  # 규칙 근거를 함께 가짐
        self.assertEqual(both["theme"], "여행")
        self.assertNotIn("RISK", [pick["symbol"] for pick in picks])
        old = {"themes": themes, "ai_picks": ai}
        self.assertEqual(len(with_combined(old)["combined_picks"]), 3)  # 예전 캐시에도 채움


class PortfolioAutoRecommendTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_recommendation_is_computed_then_used(self):
        from unittest import mock

        from app.api import insight_routes, virtual_routes

        computed = {"generated_at": "now", "ai_picks": [{"symbol": "JNJ", "name": "존슨 앤 존슨"}], "themes": []}
        request = mock.Mock()
        request.app.state.toss_api = mock.Mock()
        payload = virtual_routes.PortfolioInput(market="us", capital_krw=1_000_000, source="ai")
        with mock.patch.object(virtual_routes, "latest_recommendations", new=mock.AsyncMock(return_value=None)), \
                mock.patch.object(insight_routes, "recommendations", new=mock.AsyncMock(return_value=computed)) as compute, \
                mock.patch.object(virtual_routes, "usd_krw_rate", new=mock.AsyncMock(return_value={"mid": 1350.0})), \
                mock.patch.object(virtual_routes, "create_portfolio", new=mock.AsyncMock(return_value={"id": "x"})) as create:
            result = await virtual_routes.new_portfolio(request, payload, {"username": "alice"})
        compute.assert_awaited_once()  # 추천이 없어 지금 계산
        self.assertIs(create.call_args.args[5], computed)  # 계산한 추천으로 만듦
        self.assertEqual(result["portfolio"], {"id": "x"})


class PortfolioNameTest(unittest.IsolatedAsyncioTestCase):
    def test_default_and_unique_names(self):
        from app.virtual import clean_name, default_name, unique_name

        self.assertEqual(default_name("kr", "all", date(2026, 11, 15)), "국장 통합 추천 11-15")
        existing = [{"name": "내 포트"}, {"name": "내 포트 (2)"}]
        self.assertEqual(unique_name("내 포트", existing), "내 포트 (3)")
        self.assertEqual(unique_name("새 이름", existing), "새 이름")
        self.assertEqual(clean_name("  두   칸  "), "두 칸")
        self.assertEqual(len(clean_name("가" * 100)), 40)

    async def test_create_many_and_rename(self):
        from unittest import mock

        from app import virtual

        fake = FakeRedis()
        recommendations = {"ai_picks": [{"symbol": "A", "name": "에이"}], "themes": []}
        with mock.patch.object(virtual, "redis", fake), \
                mock.patch.object(virtual, "get_json", new=mock.AsyncMock(side_effect=lambda key: __import__("json").loads(fake.data[key]) if key in fake.data else None)):
            first = await virtual.create_portfolio("alice", "kr", 1_000_000, "ai", date(2026, 11, 17), recommendations, None)
            second = await virtual.create_portfolio("alice", "kr", 1_000_000, "ai", date(2026, 11, 17), recommendations, None)
            named = await virtual.create_portfolio("alice", "kr", 1_000_000, "ai", date(2026, 11, 17), recommendations, None, name="공격형")
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(second["name"], f"{first['name']} (2)")  # 같은 설정을 여러 번 만들 수 있고 이름은 겹치지 않게
            self.assertEqual(named["name"], "공격형")
            renamed = await virtual.rename_portfolio("alice", first["id"], "공격형")
            self.assertEqual(renamed["name"], "공격형 (2)")
            self.assertEqual(len(await virtual.list_portfolios("alice")), 3)


class PortfolioSessionTest(unittest.TestCase):
    def _portfolio(self, market):
        return {"id": "p", "market": market, "currency": "KRW" if market == "KR" else "USD", "created_date": "2026-11-15",
                "end_date": "2026-11-17", "capital": 1_000_000, "picks": [{"symbol": "A", "name": "에이"}]}

    def test_end_day_during_session_is_not_finished(self):
        from datetime import datetime

        from app.briefing import KST
        from app.virtual import evaluate

        prices = {"A": [{"date": "2026-11-16", "open": 100, "close": 105}, {"date": "2026-11-17", "open": 105, "close": 108}]}
        during = evaluate(self._portfolio("KR"), prices, [], "2026-11-17", now=datetime(2026, 11, 17, 10, 0, tzinfo=KST))
        self.assertEqual(during["status"], "active")  # 종료일 오전 장중: 아직 종료 아님
        self.assertTrue(during["live"])
        after = evaluate(self._portfolio("KR"), prices, [], "2026-11-17", now=datetime(2026, 11, 17, 16, 0, tzinfo=KST))
        self.assertEqual(after["status"], "finished")
        self.assertFalse(after["live"])

    def test_us_session_ends_next_morning_in_korea(self):
        from datetime import datetime

        from app.briefing import KST
        from app.virtual import evaluate

        prices = {"A": [{"date": "2026-11-16", "open": 100, "close": 101}, {"date": "2026-11-17", "open": 101, "close": 103}]}
        night = evaluate(self._portfolio("US"), prices, [], "2026-11-18", now=datetime(2026, 11, 18, 1, 0, tzinfo=KST))
        self.assertEqual(night["status"], "active")  # 한국 수요일 새벽 1시: 미국 화요일 장 진행 중
        self.assertTrue(night["live"])
        morning = evaluate(self._portfolio("US"), prices, [], "2026-11-18", now=datetime(2026, 11, 18, 7, 0, tzinfo=KST))
        self.assertEqual(morning["status"], "finished")

    def test_between_sessions_shows_close(self):
        from datetime import datetime

        from app.briefing import KST
        from app.virtual import evaluate

        prices = {"A": [{"date": "2026-11-16", "open": 100, "close": 105}]}
        evening = evaluate(self._portfolio("KR"), prices, [], "2026-11-16", now=datetime(2026, 11, 16, 20, 0, tzinfo=KST))
        self.assertEqual(evening["status"], "active")
        self.assertFalse(evening["live"])  # 월요일 저녁: 월요일 종가로 확정, 장중 아님


def dated(closes, volumes=None, start=date(2026, 9, 1)):
    return [{"date": (start + timedelta(days=index)).isoformat(), "open": close, "close": close,
             "volume": (volumes[index] if volumes else 1000)} for index, close in enumerate(closes)]


class ExitSignalTest(unittest.TestCase):
    def test_trail_from_peak_is_sell(self):
        from app.exit_signals import exit_signals

        result = exit_signals(dated([100, 110, 120, 105]), 100, "2026-09-01")
        self.assertEqual(result["level"], "sell")  # 최고 120에서 −12.5%
        self.assertEqual(result["signals"][0]["key"], "trail")

    def test_loss_and_weak_evidence_are_watch_only(self):
        from app.exit_signals import exit_signals

        loss = exit_signals(dated([100, 98, 88]), 100, "2026-09-01")
        self.assertEqual(loss["level"], "watch")  # 손절은 시뮬레이션에서 손해라 주의만
        self.assertEqual([signal["key"] for signal in loss["signals"]], ["loss"])
        weak = exit_signals(dated([100, 101, 102]), 100, "2026-09-01", evidence_score=40)
        self.assertEqual([signal["key"] for signal in weak["signals"]], ["evidence"])

    def test_small_gain_without_arm_is_hold(self):
        from app.exit_signals import exit_signals

        result = exit_signals(dated([100, 103, 101]), 100, "2026-09-01")
        self.assertEqual(result["level"], "hold")  # +5%를 넘은 적이 없으면 고점 대비 신호를 보지 않음
        self.assertEqual(exit_signals([], 100)["level"], "hold")

    def test_trend_break_with_volume(self):
        from app.exit_signals import exit_signals

        closes = [100 + index * 0.1 for index in range(25)] + [99]
        volumes = [1000] * 25 + [2000]
        result = exit_signals(dated(closes, volumes), 99, "2026-09-25")
        self.assertIn("trend", [signal["key"] for signal in result["signals"]])


class RecommendRuleTest(unittest.TestCase):
    def test_passes_rules(self):
        from app.simulation import passes_rules

        self.assertTrue(passes_rules(60, 0.05, 0.10))
        self.assertFalse(passes_rules(50, 0.05, 0.10))  # 근거 약함
        self.assertFalse(passes_rules(70, 0.25, 0.10))  # 5일 +20% 초과
        self.assertFalse(passes_rules(70, 0.05, 0.35))  # 20일 +30% 초과
        self.assertTrue(passes_rules(70, None, None))

    def test_inverse_vol_weights_capped(self):
        from app.simulation import position_weights

        weights = position_weights([1, 2, 2, 4, 4], "inverse_vol")
        self.assertAlmostEqual(sum(weights), 1)
        self.assertLessEqual(max(weights), 0.25 + 1e-9)
        self.assertGreater(weights[1], weights[3])  # 변동성이 작으면 더 많이
        self.assertEqual(position_weights([1, 2], "equal"), [0.5, 0.5])
        self.assertAlmostEqual(sum(position_weights([None, 2, 3], "inverse_vol")), 1)  # 모르는 값은 평균으로

    def test_exit_path_return(self):
        from app.simulation import exit_path_return

        ret, index, reason = exit_path_return(100, [110, 120, 107, 130], trail=0.10)
        self.assertEqual((index, reason), (2, "trail"))
        self.assertAlmostEqual(ret, 0.07)
        self.assertEqual(exit_path_return(100, [97, 92, 89], stop=0.10)[1:], (2, "stop"))
        ret, index, reason = exit_path_return(100, [102, 93, 95], trail=0.05)
        self.assertIsNone(index)  # +5%를 넘은 적이 없어 고점 대비 매도 안 함
        self.assertAlmostEqual(ret, -0.05)

    def test_portfolio_follow_signals_records_exits(self):
        from app.simulation import MIN_HISTORY
        from app.virtual import run_portfolio

        length = MIN_HISTORY + 45
        bumpy = [100 + index * 0.5 for index in range(MIN_HISTORY)] + [130, 140, 150, 120, 118] * 9
        series = {f"UP{n}": _candles([100 + index * (0.3 + n * 0.05) for index in range(length)]) for n in range(3)}
        series["BUMP"] = _candles(bumpy[:length])
        result = run_portfolio(series, {}, 1_000_000, horizon=20, top=4, trail=0.10)
        self.assertEqual(result["rules"]["trail"], 0.10)
        trades = [trade for period in result["periods"] for trade in period["trades"]]
        self.assertTrue(all("weight" in trade for trade in trades))
        self.assertEqual(result["early_exits"], sum(1 for trade in trades if trade["exit"]))


class PortfolioRuleTest(unittest.IsolatedAsyncioTestCase):
    def test_auto_sell_locks_value_after_signal(self):
        from app.virtual import evaluate

        portfolio = {"id": "p", "market": "KR", "currency": "KRW", "created_date": "2026-11-01", "end_date": "2026-11-06",
                     "capital": 1_000_000, "rules": {"auto_sell": True, "trail": 0.10},
                     "picks": [{"symbol": "A", "name": "에이", "weight": 0.5}, {"symbol": "B", "name": "비", "weight": 0.5}]}
        prices = {
            "A": [{"date": f"2026-11-0{day}", "open": 100 if day == 2 else close, "close": close}
                  for day, close in zip(range(2, 7), [100, 120, 105, 90, 95])],
            "B": [{"date": f"2026-11-0{day}", "open": 100, "close": 100} for day in range(2, 7)],
        }
        result = evaluate(portfolio, prices, [], "2026-11-07")
        holdings = {holding["symbol"]: holding for holding in result["holdings"]}
        self.assertEqual(holdings["A"]["status"], "sold")
        self.assertEqual(holdings["A"]["sold_date"], "2026-11-04")  # 120 → 105(−12.5%)인 날 종가에 판 것으로
        self.assertEqual(holdings["A"]["last_price"], 105)
        self.assertEqual(result["sold_count"], 1)
        values = {row["date"]: row["value"] for row in result["series"]}
        self.assertEqual(values["2026-11-04"], values["2026-11-06"])  # 판 뒤에는 값이 고정(B는 그대로)
        self.assertEqual(holdings["B"]["signal"]["level"], "hold")
        manual = evaluate({**portfolio, "rules": {"auto_sell": False}}, prices, [], "2026-11-07")
        manual_a = next(holding for holding in manual["holdings"] if holding["symbol"] == "A")
        self.assertEqual(manual_a["status"], "bought")
        self.assertEqual(manual_a["signal"]["level"], "sell")  # 자동 매도를 끄면 신호만 보여 줌
        self.assertEqual(manual["signal_counts"]["sell"], 1)

    async def test_short_hold_filters_overheated_and_sets_weights(self):
        from datetime import datetime
        from unittest import mock

        from app import virtual
        from app.briefing import KST

        fake = FakeRedis()
        recommendations = {"ai_picks": [
            {"symbol": "HOT", "name": "과열", "evidence": {"score": 80}, "rates": {"5d": 25.0, "20d": 40.0}, "volatility": 4.0},
            {"symbol": "WEAK", "name": "약함", "evidence": {"score": 40}, "rates": {"5d": 1.0, "20d": 2.0}, "volatility": 2.0},
            {"symbol": "OK1", "name": "좋음1", "evidence": {"score": 70}, "rates": {"5d": 2.0, "20d": 5.0}, "volatility": 1.0},
            {"symbol": "OK2", "name": "좋음2", "evidence": {"score": 65}, "rates": {"5d": 3.0, "20d": 6.0}, "volatility": 3.0},
            {"symbol": "OK3", "name": "좋음3", "evidence": {"score": 60}, "rates": {"5d": 1.0, "20d": 3.0}, "volatility": 2.5},
        ], "themes": []}
        today = datetime.now(KST).date()
        with mock.patch.object(virtual, "redis", fake), \
                mock.patch.object(virtual, "get_json", new=mock.AsyncMock(side_effect=lambda key: __import__("json").loads(fake.data[key]) if key in fake.data else None)):
            short = await virtual.create_portfolio("alice", "kr", 1_000_000, "ai", today + timedelta(days=3), recommendations, None, auto_sell=True)
            long = await virtual.create_portfolio("alice", "kr", 1_000_000, "ai", today + timedelta(days=40), recommendations, None, weak_market=True)
            weak = await virtual.create_portfolio("alice", "kr", 1_000_000, "ai", today + timedelta(days=3), recommendations, None, weak_market=True)
        self.assertAlmostEqual(sum(pick["weight"] for pick in weak["picks"]), 0.5, places=3)  # 단기 + 약세장: 절반만 투자
        self.assertTrue(weak["rules"]["weak_market"])
        self.assertFalse(long["rules"]["weak_market"])  # 길게 들고 있으면 약세장이어도 그대로
        self.assertEqual([pick["symbol"] for pick in short["picks"]], ["OK1", "OK2", "OK3"])
        self.assertEqual({item["symbol"] for item in short["excluded"]}, {"HOT", "WEAK"})
        self.assertTrue(short["rules"]["filtered"] and short["rules"]["auto_sell"])
        self.assertEqual(len(long["picks"]), 5)  # 길게 들고 있으면 거르지 않음
        weights = {pick["symbol"]: pick["weight"] for pick in long["picks"]}
        self.assertGreater(weights["OK1"], weights["HOT"])  # 변동성이 작은 쪽이 더 많이
        self.assertFalse(long["rules"]["filtered"])
        self.assertAlmostEqual(sum(pick["weight"] for pick in long["picks"]), 1, places=3)

    def test_managed_sells_on_signal_and_buys_latest_pick(self):
        from app.managed import simulate_managed

        def bars(closes, start=2):
            return [{"date": f"2026-11-{day:02d}", "open": close, "close": close} for day, close in zip(range(start, start + len(closes)), closes)]

        portfolio = {"id": "p", "market": "KR", "source": "ai", "created_date": "2026-11-01", "capital": 1_000_000,
                     "picks": [{"symbol": "A", "name": "에이", "weight": 0.5}, {"symbol": "B", "name": "비", "weight": 0.5}]}
        index = bars([100] * 6)
        snapshots = [{"date": "2026-11-03", "generated_at": "2026-11-03 18:00",
                      "items": [{"symbol": "A", "name": "에이", "kind": "ai_pick"}, {"symbol": "C", "name": "씨", "kind": "ai_pick"}]}]
        prices = {"A": bars([100, 120, 105, 100, 100, 100]), "B": bars([100] * 6)}
        need = simulate_managed(portfolio, prices, index, snapshots, "2026-11-07", 0.0, 0.10, 0.05)
        self.assertEqual(need, {"missing": ["C"]})  # 새로 살 종목 가격이 없으면 달라고 함
        prices["C"] = bars([50, 50, 50, 60, 60, 66])
        run = simulate_managed(portfolio, prices, index, snapshots, "2026-11-07", 0.0, 0.10, 0.05)
        actions = [(trade["date"], trade["action"], trade["symbol"]) for trade in run["trades"]]
        # A: 120 → 105(−12.5%)인 11-04 종가에 팔고, 11-05 시가에 11-03 추천 중 안 가진 C를 판 돈으로 삼(A는 방금 팔아 제외)
        self.assertIn(("2026-11-04", "sell", "A"), actions)
        self.assertIn(("2026-11-05", "buy", "C"), actions)
        held = {holding["symbol"]: holding for holding in run["holdings"]}
        self.assertEqual(set(held), {"B", "C"})
        self.assertEqual(held["C"]["shares"], math.floor(500_000 * 1.05 / 60))  # 판 돈(50만원 × 105/100)으로
        self.assertAlmostEqual(run["series"][-1]["value"], run["cash"] + 5_000 * 100 + held["C"]["shares"] * 66, places=2)
        self.assertEqual(run["closed"][0]["exit_reason"], "고점 대비 −10%")

    def test_managed_rotate_replaces_dropped_holdings(self):
        from app.managed import simulate_managed

        def bars(closes):
            return [{"date": f"2026-11-{day:02d}", "open": close, "close": close} for day, close in zip(range(2, 2 + len(closes)), closes)]

        portfolio = {"id": "p", "market": "KR", "source": "all", "created_date": "2026-11-01", "capital": 1_000_000,
                     "picks": [{"symbol": "A", "name": "에이"}, {"symbol": "B", "name": "비"}]}
        snapshots = [{"date": "2026-11-01", "generated_at": "2026-11-01 18:00", "items": [{"symbol": "A", "name": "에이", "kind": "combined"}]},
                     {"date": "2026-11-03", "generated_at": "2026-11-03 18:00",
                      "items": [{"symbol": "A", "name": "에이", "kind": "combined"}, {"symbol": "D", "name": "디", "kind": "combined"}]}]
        prices = {symbol: bars([100] * 5) for symbol in ("A", "B", "D")}
        kept = simulate_managed(portfolio, prices, bars([100] * 5), snapshots, "2026-11-06", 0.0, 0.10, 0.05)
        self.assertEqual({holding["symbol"] for holding in kept["holdings"]}, {"A", "B"})  # 교체를 안 켜면 그대로
        run = simulate_managed(portfolio, prices, bars([100] * 5), snapshots, "2026-11-06", 0.0, 0.10, 0.05, rotate=True)
        self.assertEqual({holding["symbol"] for holding in run["holdings"]}, {"A", "D"})
        sold = run["closed"][0]
        self.assertEqual((sold["symbol"], sold["sold_date"]), ("B", "2026-11-04"))  # 11-03 저녁 추천 → 다음 날 시가에 교체
        self.assertIn("새 추천에서 빠짐", sold["exit_reason"])
        empty = snapshots + [{"date": "2026-11-04", "generated_at": "2026-11-04 18:00", "items": []}]
        same = simulate_managed(portfolio, prices, bars([100] * 5), empty, "2026-11-06", 0.0, 0.10, 0.05, rotate=True)
        self.assertEqual({holding["symbol"] for holding in same["holdings"]}, {"A", "D"})  # 빈 추천 기록으로 전부 팔지 않음
        # 추천이 고른 수를 따름: 새 추천이 3종목(A·E·F)이면 빠진 B를 팔고 그 돈을 E·F에 반씩
        wide = [snapshots[0], {"date": "2026-11-03", "generated_at": "2026-11-03 18:00", "items": [
            {"symbol": symbol, "name": symbol, "kind": "ai_pick"} for symbol in ("A", "E", "F")]}]
        prices.update({"E": bars([100] * 5), "F": bars([100] * 5)})
        grown = simulate_managed({**portfolio, "source": "ai"}, prices, bars([100] * 5), wide, "2026-11-06", 0.0, 0.10, 0.05, rotate=True)
        self.assertEqual({holding["symbol"]: holding["shares"] for holding in grown["holdings"]}, {"A": 5000, "E": 2500, "F": 2500})
        fixed = simulate_managed({**portfolio, "source": "ai", "requested_count": 2}, prices, bars([100] * 5), wide, "2026-11-06", 0.0, 0.10, 0.05, rotate=True)
        self.assertEqual(len(fixed["holdings"]), 2)  # 수를 정했으면 그 수 유지

    def test_live_tick_trades_intraday(self):
        from app.managed import live_tick, needed_symbols

        portfolio = {"id": "p", "market": "KR", "source": "ai", "created_date": "2026-11-24", "capital": 1_000_000,
                     "picks": [{"symbol": "A", "name": "에이", "weight": 0.5}, {"symbol": "B", "name": "비", "weight": 0.5}]}
        snapshots = [{"date": "2026-11-24", "generated_at": "2026-11-24 16:00",
                      "items": [{"symbol": "A", "name": "에이", "kind": "ai_pick"}, {"symbol": "C", "name": "씨", "kind": "ai_pick"}]}]
        args = (0.0, 0.10, 0.05, False)
        # 처음 확인(장중 09:05): 지금 가격으로 처음 종목을 삼
        state = live_tick({}, portfolio, {"A": 100, "B": 50}, snapshots, "2026-11-25", "2026-11-25 09:05", *args)
        self.assertEqual({symbol: item["shares"] for symbol, item in state["held"].items()}, {"A": 5000, "B": 10000})
        self.assertEqual(state["trades"][0]["time"], "2026-11-25 09:05")
        self.assertEqual(set(needed_symbols(state, portfolio, snapshots, "2026-11-25 09:10")), {"A", "B", "C"})
        # 장중에 A가 120까지 올랐다가 107(−10.8%)로 → 그 시점 가격에 팜
        state = live_tick(state, portfolio, {"A": 120, "B": 50, "C": 40}, snapshots, "2026-11-25", "2026-11-25 10:00", *args)
        state = live_tick(state, portfolio, {"A": 107, "B": 50, "C": 40}, snapshots, "2026-11-25", "2026-11-25 10:05", *args)
        self.assertNotIn("A", state["held"])
        self.assertEqual(state["closed"][-1]["sold_time"], "2026-11-25 10:05")
        self.assertEqual(state["pending"], [535_000])
        # 다음 확인(5분 뒤)에 판 돈으로 추천 중 안 가진 C를 지금 가격에 삼(방금 판 A는 제외)
        state = live_tick(state, portfolio, {"A": 107, "B": 50, "C": 40}, snapshots, "2026-11-25", "2026-11-25 10:10", *args)
        self.assertEqual(state["held"]["C"]["shares"], 13_375)
        self.assertEqual(state["pending"], [])
        self.assertEqual([point["date"] for point in state["series"]], ["2026-11-25"])  # 같은 날은 평가액 한 점을 덮어씀

    def test_decision_rank_buys_uses_market_and_recommendation(self):
        from app.decision import Context, rank_buys

        snapshot = {"items": [
            {"symbol": "GOOD", "name": "좋음", "kind": "ai_pick", "evidence": 70, "theme": "여행"},
            {"symbol": "OK", "name": "보통", "kind": "pick", "evidence": 58, "theme": "여행"},
            {"symbol": "LOW", "name": "약함", "kind": "pick", "evidence": 45, "theme": "여행"},
            {"symbol": "RISKY", "name": "위험", "kind": "ai_pick", "evidence": 80, "theme": "여행"},
            {"symbol": "RISKY", "name": "위험", "kind": "ai_risk", "evidence": 80, "theme": "여행"},
            {"symbol": "FALL", "name": "하락테마", "kind": "pick", "evidence": 75, "theme": "전자부품"},
            {"symbol": "HOT", "name": "과열", "kind": "pick", "evidence": 75, "theme": "여행"},
        ]}
        options = [(item["symbol"], item["name"]) for item in snapshot["items"]]
        tech = {"HOT": {"r5": 0.25, "r20": 0.1}, "OK": {"above_ma20": False}}
        ctx = Context(flows={"전자부품": "하락 지속", "여행": "상승 지속"}, tech=lambda symbol: tech.get(symbol))
        ranked = [symbol for symbol, _, _ in rank_buys(list(dict.fromkeys(options)), snapshot, ctx)]
        self.assertEqual(ranked, ["GOOD", "OK"])  # 위험 지정·하락 테마·과열·근거 50 미만은 빼고, AI 추천 가산
        weak = Context(weak_market=True, flows=ctx.flows, tech=ctx.tech)
        self.assertEqual([symbol for symbol, _, _ in rank_buys(list(dict.fromkeys(options)), snapshot, weak)], ["GOOD"])  # 약세장은 60점 이상만

    def test_decision_exit_reasons(self):
        from app.decision import Context, exit_reason

        item = {"symbol": "A", "buy_price": 100, "peak": 110, "theme": "여행"}
        calm = Context(late=True, tech=lambda symbol: {"above_ma20": True, "r20": 0.02, "rsi": 55})
        self.assertIsNone(exit_reason(item, 104, {"items": [{"symbol": "A", "kind": "pick"}]}, calm, 0.10, 0.05))
        self.assertIn("약세장", exit_reason(item, 102, None, Context(weak_market=True), 0.10, 0.05))  # 110 → 102(−7.3%)
        risk = {"items": [{"symbol": "A", "kind": "ai_risk"}]}
        self.assertIn("위험 종목", exit_reason(item, 104, risk, Context(late=False), 0.10, 0.05))
        reviewed = Context(late=False, review={"A": {"action": "sell", "reason": "근거 약화"}})
        self.assertEqual(exit_reason(item, 104, None, reviewed, 0.10, 0.05), "AI 점검: 근거 약화")
        broken = Context(late=True, tech=lambda symbol: {"above_ma20": False, "r20": -0.08, "rsi": 40})
        self.assertIn("추천 근거 약화", exit_reason({**item, "peak": 100}, 95, {"items": []}, broken, 0.10, 0.05))
        self.assertIsNone(exit_reason({**item, "peak": 100}, 95, {"items": []}, Context(late=False, tech=broken.tech), 0.10, 0.05))  # 장중엔 안 봄
        falling = Context(late=True, flows={"여행": "하락 전환"}, tech=lambda symbol: {"above_ma20": False, "r20": 0.0})
        self.assertIn("테마 흐름 하락", exit_reason({**item, "peak": 100}, 101, {"items": [{"symbol": "A", "kind": "pick"}]}, falling, 0.10, 0.05))
        hot = Context(late=True, tech=lambda symbol: {"above_ma20": True, "r20": 0.3, "rsi": 85})
        self.assertIn("이익 실현", exit_reason({**item, "peak": 130}, 130, {"items": [{"symbol": "A", "kind": "pick"}]}, hot, 0.10, 0.05))

    def test_weak_market_buys_half_and_reinvests_later(self):
        from app.decision import Context
        from app.managed import live_tick

        portfolio = {"id": "p", "market": "KR", "source": "ai", "created_date": "2026-11-24", "capital": 1_000_000,
                     "picks": [{"symbol": "A", "name": "에이", "weight": 1.0}]}
        snapshots = [{"date": "2026-11-24", "generated_at": "2026-11-24 16:00",
                      "items": [{"symbol": "A", "name": "에이", "kind": "ai_pick", "evidence": 70},
                                {"symbol": "C", "name": "씨", "kind": "ai_pick", "evidence": 70}]}]
        args = (0.0, 0.10, 0.05, False)
        state = live_tick({}, portfolio, {"A": 100, "C": 100}, snapshots, "2026-11-25", "2026-11-25 09:05", *args)
        state = live_tick(state, portfolio, {"A": 120, "C": 100}, snapshots, "2026-11-25", "2026-11-25 09:10", *args)
        weak = Context(weak_market=True)
        state = live_tick(state, portfolio, {"A": 111, "C": 100}, snapshots, "2026-11-25", "2026-11-25 09:15", *args, ctx=weak)  # 약세장 −7.5%로 매도
        self.assertNotIn("A", state["held"])
        state = live_tick(state, portfolio, {"A": 111, "C": 100}, snapshots, "2026-11-25", "2026-11-25 09:20", *args, ctx=weak)
        self.assertEqual(state["held"]["C"]["shares"], 5550)  # 판 돈 111만원의 절반만
        self.assertAlmostEqual(state["reserve"], 555_000)
        self.assertIn("근거 70점", state["held"]["C"]["buy_reason"])
        state = live_tick(state, portfolio, {"A": 111, "C": 100, "D": 50}, [{**snapshots[0], "items": snapshots[0]["items"] + [
            {"symbol": "D", "name": "디", "kind": "ai_pick", "evidence": 65}]}], "2026-11-26", "2026-11-26 09:05", *args, ctx=Context())
        self.assertEqual(state["reserve"], 0)  # 시장이 회복되면 남겨 둔 돈으로 추천 종목을 삼
        self.assertEqual(state["held"]["D"]["shares"], 11_100)

    def test_intraday_refresh_changes_portfolio_gently(self):
        from app.managed import live_tick

        portfolio = {"id": "p", "market": "KR", "source": "all", "created_date": "2026-11-24", "capital": 1_000_000,
                     "picks": [{"symbol": "A", "name": "A", "weight": 0.5}, {"symbol": "B", "name": "B", "weight": 0.5}]}
        morning = {"date": "2026-11-25", "generated_at": "2026-11-25 08:00", "items": [
            {"symbol": "A", "name": "A", "kind": "combined"}, {"symbol": "B", "name": "B", "kind": "combined"}]}
        args = (0.0, 0.10, 0.05, True)
        quotes = {"A": 100, "B": 100, "C": 100, "D": 100}
        state = live_tick({}, portfolio, quotes, [morning], "2026-11-25", "2026-11-25 09:05", *args)
        # 10:30 장중 갱신: B는 통합에서 빠졌지만 테마 후보(pick)에 남음 → 유지, A는 완전히 빠짐 → 팔고 새 추천 C를 삼
        noon = {"date": "2026-11-25", "generated_at": "2026-11-25 10:30", "items": [
            {"symbol": "C", "name": "C", "kind": "combined"}, {"symbol": "B", "name": "B", "kind": "pick"}]}
        state = live_tick(state, portfolio, quotes, [morning, noon], "2026-11-25", "2026-11-25 10:35", *args)
        self.assertEqual(set(state["held"]), {"B", "C"})
        self.assertEqual(state["used_snapshot"], "2026-11-25 10:30")
        # 장중에 매도 신호로 판 종목은 그날 다시 나온 추천에 있어도 다시 사지 않음
        state = live_tick(state, portfolio, {**quotes, "C": 120}, [morning, noon], "2026-11-25", "2026-11-25 11:00", *args)
        state = live_tick(state, portfolio, {**quotes, "C": 105}, [morning, noon], "2026-11-25", "2026-11-25 11:05", *args)
        self.assertNotIn("C", state["held"])
        later = {**noon, "generated_at": "2026-11-25 11:30", "items": noon["items"] + [{"symbol": "D", "name": "D", "kind": "combined"}]}
        state = live_tick(state, portfolio, quotes, [morning, noon, later], "2026-11-25", "2026-11-25 11:35", *args)
        self.assertNotIn("C", state["held"])
        self.assertIn("D", state["held"])
        # 예전 장부(날짜만 남김)는 같은 기록을 새 추천으로 보지 않음
        legacy = {**state, "used_snapshot": "2026-11-25", "banned": ["C"]}
        again = live_tick(legacy, portfolio, quotes, [later], "2026-11-25", "2026-11-25 11:40", *args)
        self.assertEqual(again["banned"], ["C"])
        self.assertEqual(again["used_snapshot"], "2026-11-25 11:30")

    def test_review_sell_blocks_buy_and_refresh_timing(self):
        from datetime import datetime

        from app.api.insight_routes import intraday_refresh_due
        from app.briefing import KST
        from app.decision import Context, rank_buys

        snapshot = {"items": [{"symbol": "JNJ", "name": "J", "kind": "ai_pick", "evidence": 70}]}
        self.assertEqual(rank_buys([("JNJ", "J")], snapshot, Context(review={"JNJ": {"action": "sell"}})), [])
        at = lambda text: datetime.fromisoformat(text).replace(tzinfo=KST)  # noqa: E731
        self.assertFalse(intraday_refresh_due("kr", at("2026-11-25T09:10")))  # 장 시작 20분 안
        self.assertTrue(intraday_refresh_due("kr", at("2026-11-25T09:25")))
        self.assertFalse(intraday_refresh_due("kr", at("2026-11-25T16:00")))
        self.assertTrue(intraday_refresh_due("us", at("2026-11-26T00:30")))  # 뉴욕 10:30

    def test_preorder_plan_and_open_execution(self):
        from datetime import datetime, time

        from app.briefing import KST
        from app.decision import Context
        from app.managed import execute_plan, live_tick, next_session_day, plan_orders

        portfolio = {"id": "p", "market": "KR", "source": "ai", "created_date": "2026-11-23", "capital": 1_000_000,
                     "picks": [{"symbol": "A", "name": "A", "weight": 1.0}]}
        snapshots = [{"date": "2026-11-24", "generated_at": "2026-11-24 08:00", "items": [{"symbol": "A", "name": "A", "kind": "ai_pick", "evidence": 70}]},
                     {"date": "2026-11-25", "generated_at": "2026-11-25 15:45", "items": [
                         {"symbol": "A", "name": "A", "kind": "ai_pick", "evidence": 70}, {"symbol": "C", "name": "C", "kind": "ai_pick", "evidence": 70}]}]
        args = (0.0, 0.10, 0.05, False)
        state = live_tick({}, portfolio, {"A": 100}, snapshots[:1], "2026-11-24", "2026-11-24 09:05", *args)
        state = live_tick(state, portfolio, {"A": 120}, snapshots[:1], "2026-11-25", "2026-11-25 14:00", *args)
        # 장 마감 뒤(종가 109): 고점 120 대비 −9.2%라 아직 아님 → AI 점검이 매도 의견 → A 예약 매도, 판 돈으로 C 예약 매수
        ctx = Context(review={"A": {"action": "sell", "reason": "근거 약화"}})
        plan = plan_orders(state, portfolio, {"A": 109, "C": 50}, snapshots, "2026-11-26", "2026-11-25 16:00", *args, ctx)
        self.assertEqual([(order["action"], order["symbol"]) for order in plan["orders"]], [("sell", "A"), ("buy", "C")])
        self.assertEqual(plan["orders"][1]["limit"], 51.5)
        self.assertEqual(state["held"]["A"]["shares"], 10_000)  # 예약만, 장부는 그대로
        state = {**state, "plan": plan}
        # 다음 날 시가: A 108에 팔고, C가 52(전날 50 대비 +4%, 갭 상승)로 시작하면 사지 않고 그날은 쫓아 사지 않음
        gap = execute_plan(state, portfolio, {"A": {"open": 108, "prev_close": 109}, "C": {"open": 52, "prev_close": 50}},
                           snapshots, "2026-11-26", "2026-11-26 09:05", *args, Context())
        self.assertNotIn("A", gap["held"])
        self.assertNotIn("C", gap["held"])
        self.assertIn("C", gap["banned"])
        self.assertTrue(gap["plan"]["executed"])
        self.assertEqual(gap["plan"]["skipped"][0]["symbol"], "C")
        self.assertTrue(gap["trades"][-1]["reason"].startswith("예약(시가 매도)"))
        calm = execute_plan(state, portfolio, {"A": {"open": 108, "prev_close": 109}, "C": {"open": 51, "prev_close": 50}},
                            snapshots, "2026-11-26", "2026-11-26 09:05", *args, Context())
        self.assertEqual(calm["held"]["C"]["shares"], math.floor(1_080_000 / 51))
        self.assertTrue(calm["held"]["C"]["buy_reason"].startswith("예약(시가 매수)"))
        # 다음 장 날짜: 금요일 장 마감 뒤 → 월요일, 장 시작 전 → 오늘
        friday = datetime(2026, 11, 27, 16, 0, tzinfo=KST)
        self.assertEqual(next_session_day("KR", friday, time(9, 0)), "2026-11-30")
        self.assertEqual(next_session_day("KR", friday.replace(hour=8), time(9, 0)), "2026-11-27")

    def test_stock_memory_merge_grade_and_summary(self):
        from app.stock_memory import MEMORY_MAX, events_from_recommendation, merge, outcome_for, summarize

        result = {"method": "ai", "themes": [{"kind": "trend", "name": "여행", "stocks": [
            {"symbol": "A", "name": "에이", "close": 100, "evidence": {"score": 70}}]}],
                  "ai_picks": [{"symbol": "A", "name": "에이", "close": 100, "evidence": {"score": 70}, "reason": "추세 강함", "risk": "변동성"}],
                  "ai_risk_picks": []}
        morning = events_from_recommendation(result, "2026-11-25 09:30", "2026-11-25")
        self.assertEqual({event["type"] for event in morning}, {"pick", "ai_pick"})
        self.assertEqual(next(event for event in morning if event["type"] == "ai_pick")["reason"], "추세 강함")
        # 같은 날 장중 갱신: 한 건으로 합치고 처음 가격·시각은 유지, 이유는 최신
        noon = events_from_recommendation({**result, "ai_picks": [{**result["ai_picks"][0], "close": 104, "reason": "수급 개선"}]},
                                           "2026-11-25 12:00", "2026-11-25")
        merged = merge(merge([], morning), noon)
        ai = [event for event in merged if event["type"] == "ai_pick"]
        self.assertEqual(len(ai), 1)
        self.assertEqual((ai[0]["price"], ai[0]["at"], ai[0]["reason"]), (100, "2026-11-25 09:30", "수급 개선"))
        self.assertEqual(len(merge([], [{**morning[0], "day": f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}"} for i in range(200)])), MEMORY_MAX)
        # 채점: 판단 뒤 거래일 기준, 아직 안 지난 기간은 비움, 지수 대비도
        closes = [("2026-11-25", 100), ("2026-11-26", 102), ("2026-11-27", 99), ("2026-11-30", 101), ("2026-12-01", 103), ("2026-12-02", 110)]
        index = [(day, 1000 + offset * 10) for offset, (day, _) in enumerate(closes)]
        outcome = outcome_for(ai[0], closes, index)
        self.assertEqual(outcome["d1"], 0.02)
        self.assertEqual(outcome["d5"], 0.1)
        self.assertAlmostEqual(outcome["x5"], 0.1 - 0.05)
        self.assertNotIn("d20", outcome)
        # 요약: as_of 전에 끝난 결과만(미래 정보 없음)
        graded = [{**ai[0], "outcome": outcome}]
        self.assertEqual(summarize(graded, "2026-12-03")["ai_pick_x5"], 0.05)
        self.assertEqual(summarize(graded, "2026-11-25")["ai_pick_samples"], 0)  # 그날 판단은 아직 안 셈

    def test_market_session_hours(self):
        from datetime import datetime

        from app.briefing import KST
        from app.managed_live import market_session

        at = lambda text: datetime.fromisoformat(text).replace(tzinfo=KST)  # noqa: E731
        self.assertEqual(market_session("KR", at("2026-11-25T10:00")), (True, "2026-11-25"))
        self.assertFalse(market_session("KR", at("2026-11-25T15:31"))[0])
        self.assertFalse(market_session("KR", at("2026-11-28T10:00"))[0])  # 토요일
        self.assertEqual(market_session("US", at("2026-11-25T23:40")), (True, "2026-11-25"))  # 겨울: 뉴욕 09:40
        self.assertFalse(market_session("US", at("2026-11-25T22:40"))[0])  # 뉴욕 08:40, 아직 개장 전
        self.assertEqual(market_session("US", at("2026-11-26T05:30")), (True, "2026-11-25"))  # 뉴욕 15:30, 날짜는 미국 기준
        self.assertTrue(market_session("US", at("2026-07-01T22:40"))[0])  # 서머타임: 뉴욕 09:40
        from app.managed_live import price_day

        self.assertEqual(price_day("KR", at("2026-11-23T04:54")), "2026-11-20")  # 월요일 장 전 → 금요일 종가
        self.assertEqual(price_day("KR", at("2026-11-25T10:00")), "2026-11-25")
        self.assertEqual(price_day("KR", at("2026-11-25T16:00")), "2026-11-25")
        self.assertEqual(price_day("US", at("2026-11-25T06:15")), "2026-11-24")  # 미국 장 마감 뒤(뉴욕 전날 16:15)
        self.assertEqual(price_day("US", at("2026-11-25T20:00")), "2026-11-24")  # 뉴욕 06:00, 장 전

    def test_pick_count_can_exceed_five(self):
        from app.virtual import pick_recommendations

        result = {"combined_picks": [{"symbol": f"C{i}", "name": f"c{i}"} for i in range(5)],
                  "ai_picks": [{"symbol": "C0", "name": "c0"}, {"symbol": "A1", "name": "a1"}],
                  "themes": [{"kind": "trend", "name": "t", "stocks": [{"symbol": f"T{i}", "name": f"t{i}", "evidence": {"score": 50 + i}} for i in range(6)]}]}
        picks = pick_recommendations(result, "all", limit=10)
        self.assertEqual([pick["symbol"] for pick in picks][:6], ["C0", "C1", "C2", "C3", "C4", "A1"])  # 통합 5개 뒤로 AI → 테마
        self.assertEqual(len(picks), 10)
        self.assertEqual(picks[6]["symbol"], "T5")  # 테마는 근거 점수 높은 순
        self.assertEqual(len(pick_recommendations(result, "ai", limit=10)), 2)  # AI 추천만은 AI가 고른 수까지
        # 수를 정하지 않으면 추천이 고른 그대로: 통합 5개, AI 2개, 테마 6개
        self.assertEqual([len(pick_recommendations(result, source)) for source in ("all", "ai", "theme")], [5, 2, 6])

    def test_combined_picks_keep_every_ai_pick(self):
        from app.recommendations import combined_picks

        ai = [{"symbol": f"A{i}", "name": f"a{i}"} for i in range(8)]
        themes = [{"kind": "trend", "name": "t", "sector_id": 1, "stocks": [{"symbol": "T0", "name": "t0", "evidence": {"score": 60}}]}]
        self.assertEqual(len(combined_picks(themes, ai)), 8)  # AI가 8개를 고르면 8개 모두
        self.assertEqual(len(combined_picks(themes, ai[:2])), 3)  # 적으면 테마로 채움(테마 후보가 있는 만큼, 최대 5)

    async def test_change_end_date_moves_group_and_keeps_original(self):
        import json
        from unittest import mock

        from app import virtual

        fake = FakeRedis()
        today = date.today()
        created = (today - timedelta(days=9)).isoformat()
        first_end = (today - timedelta(days=8)).isoformat()
        items = [{"id": "aaaaaaaaaaaa", "group": "g1", "created_date": created, "end_date": first_end},
                 {"id": "bbbbbbbbbbbb", "group": "g1", "created_date": created, "end_date": first_end},
                 {"id": "cccccccccccc", "group": None, "created_date": created, "end_date": first_end}]
        fake.data[virtual._key("alice")] = json.dumps(items)
        with mock.patch.object(virtual, "redis", fake), \
                mock.patch.object(virtual, "get_json", new=mock.AsyncMock(side_effect=lambda key: json.loads(fake.data[key]) if key in fake.data else None)):
            changed = await virtual.change_end_date("alice", "aaaaaaaaaaaa", today)
            with self.assertRaises(ValueError):  # 만든 날보다 앞이면 안 됨
                await virtual.change_end_date("alice", "cccccccccccc", today - timedelta(days=20))
            with self.assertRaises(ValueError):  # 너무 먼 미래도 안 됨
                await virtual.change_end_date("alice", "cccccccccccc", today + timedelta(days=200))
            with self.assertRaises(LookupError):
                await virtual.change_end_date("alice", "dddddddddddd", today)
        self.assertEqual({item["id"] for item in changed}, {"aaaaaaaaaaaa", "bbbbbbbbbbbb"})  # 세 가지 비교 묶음은 함께
        saved = {item["id"]: item for item in json.loads(fake.data[virtual._key("alice")])}
        self.assertEqual(saved["bbbbbbbbbbbb"]["end_date"], today.isoformat())
        self.assertEqual(saved["bbbbbbbbbbbb"]["original_end_date"], first_end)
        self.assertEqual(saved["cccccccccccc"]["end_date"], first_end)  # 다른 포트폴리오는 그대로


class DailyRecordTest(unittest.TestCase):
    def test_due_record_session(self):
        from datetime import datetime

        from app.api.insight_routes import due_record_session
        from app.briefing import KST

        def at(day, hour, minute=0):
            return datetime(2026, 11, day, hour, minute, tzinfo=KST)

        self.assertIsNone(due_record_session("kr", at(17, 15, 50)))  # 마감 15분 전
        self.assertEqual(due_record_session("kr", at(17, 16, 0)), "2026-11-17")
        self.assertIsNone(due_record_session("kr", at(17, 23, 0)))  # 6시간 지나면 건너뜀
        self.assertEqual(due_record_session("us", at(18, 7, 0)), "2026-11-17")  # 미장 화요일 장은 한국 수요일 아침
        self.assertIsNone(due_record_session("kr", at(21, 16, 0)))  # 토요일
        self.assertEqual(due_record_session("us", at(21, 7, 0)), "2026-11-20")  # 토요일 아침 = 금요일 미장
        self.assertIsNone(due_record_session("us", at(23, 7, 0)))  # 월요일 아침: 일요일 미장 없음
