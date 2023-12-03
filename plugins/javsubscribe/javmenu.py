from app.utils.singleton import Singleton
from app.core.config import settings
from app.utils.http import RequestUtils
from lxml import etree
import re
import requests

class JavMenuWeb(object):
    _session = requests.Session()

    global _web_base
    _web_base = "https://javmenu.com"
    _page_limit = 50
    _timout = 5
    _proxies = settings.PROXY
    # _proxies = {
    #             "http": "http://192.168.1.10:7890",
    #             "https": "http://192.168.1.10:7890"
    #         }

    _weburls = {
        # 排行榜
        "rank_list": f"{_web_base}/zh/rank/%s/%s?page=%s"
    }

    _webparsers = {
        "jav_list": {
            "list": '//div[contains(@class,"category-page")]/div',
            "item": {
                "id": "./div/a/h5/text()",
                "date": './div/a/span[@class="text-muted"]/text()',
                "title": './div/a/p[@class="card-text text-primary"]/text()',
                "img": './a/div/img/@data-src',
            },
            "format": {
                "id": lambda x : x.replace(" ",""),
                "title": lambda x : "" if not x else x.replace("\n", "").strip()
            },
            "filter": {
                "id": lambda x : is_jav(x)
            }
        },
        "search_pagination": {
            "currentPage": ["//li[@class='page-item active']/*/text()", lambda x : int(x)],
            "nextPage": ["//li[@class='page-item active']/following-sibling::li[1]/*/text()", lambda x : int(x) if x else -1],
            "totalPage": ["//li[@class='page-item'][last()-1]/*/text()", lambda x: int(x)]
        },
    }
    
    @classmethod
    def __invoke_web(cls, url, params=(), cookies='', headers={}):
        if url in cls._weburls:
            req_url = cls._weburls.get(url)
        else:
            req_url = url
        if not req_url:
            return None
        if "user-agent" not in headers:
            headers['accept-language'] = 'zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7'
            headers['user-agent'] = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36'
        return RequestUtils(cookies=cookies,
                            session=cls._session,
                            headers=headers,
                            proxies=cls._proxies,
                            timeout=cls._timout).get(url=req_url % params)

    @classmethod
    def __invoke_json(cls, url, *kwargs):
        req_url = cls._jsonurls.get(url)
        if not req_url:
            return None
        headers = {}
        headers['accept-language'] = 'zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7'
        headers['user-agent'] = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36'
        req = RequestUtils(session=cls._session,
                            headers=headers,
                            proxies=cls._proxies,
                           timeout=cls._timout).get_res(url=req_url % kwargs)
        return req.json() if req else None

    @staticmethod
    def __get_json(json):
        if not json:
            return None
        return json.get("subjects")

    @classmethod
    def __get_list(cls, url, html):
        if not url or not html:
            return None
        xpaths = cls._webparsers.get(url)
        if not xpaths:
            return None
        items = etree.HTML(html).xpath(xpaths.get("list"))
        if not items:
            return None
        result = []
        for item in items:
            obj = {}
            for key, value in xpaths.get("item").items():
                format = lambda x:x
                filter = lambda x:True
                if xpaths.get("format") and xpaths.get("format").get(key):
                    format = xpaths.get("format").get(key)
                if xpaths.get("filter") and xpaths.get("filter").get(key):
                    filter = xpaths.get("filter").get(key)

                default = None
                if isinstance(value, list):
                    default = value[1]
                    value = value[0]
                if isinstance(value, str):
                    text = item.xpath(value)
                    if text:
                        obj[key] = format(text) if len(text) > 1 else format(text[0])
                    else:
                        obj[key] = default
                if not filter(obj[key]):
                    obj = None
                    break
            if obj:
                result.append(obj)
        return result

    @classmethod
    def __get_obj(cls, url, html):
        if not url or not html:
            return None
        xpaths = cls._webparsers.get(url)
        if not xpaths:
            return None
        obj = {}
        for key, value in xpaths.items():
            try:
                format = lambda x:x
                if isinstance(value, list) and len(value) == 2:
                    format = value[1]
                    value = value[0]
                text = etree.HTML(html).xpath(value)
                text = text[0] if text and len(text) == 1 else text
                if len(text) == 0: text = None
                obj[key] = format(text)
            except Exception as e:
                pass
                # ExceptionUtils.exception_traceback(e)
        return obj

    @classmethod
    def rank_list(cls, type="censored", rank_type="day", page=1):
        """
        排行榜
        """
        doc = cls.__invoke_web("rank_list", params=(type, rank_type, str(page)))
        jav_list = cls.__get_list("jav_list", doc)
        pagination = cls.__get_obj('search_pagination', doc)
        return {'jav_list': jav_list if jav_list else [], 'pagination': pagination}
    
    @classmethod
    def page_jav_list(cls, page_url):
        """
        获取列表
        """
        doc = cls.__invoke_web(page_url)
        jav_list = cls.__get_list("jav_list", doc)
        pagination = cls.__get_obj('search_pagination', doc)
        return {'jav_list': jav_list if jav_list else [], 'pagination': pagination}
        
    def __bytes(size):
        # 'b' | 'gb' | 'kb' | 'mb' | 'pb' | 'tb' | 'B' | 'GB' | 'KB' | 'MB' | 'PB' | 'TB'
        if not size:
            return 0
        size = size.upper()
        num = eval(re.sub(u"([^\u0030-\u0039\u002e])", "", size))
        if 'PB' in size:
            return round(num * 1024 * 1024 * 1024 * 1024 * 1024)
        elif 'TB' in size:
            return round(num * 1024 * 1024 * 1024 * 1024)
        elif 'GB' in size:
            return round(num * 1024 * 1024 * 1024)
        elif 'MB' in size:
            return round(num * 1024 * 1024)
        elif 'KB' in size:
            return round(num * 1024)
        elif 'B' in size:
            return round(num)
        return 0
    
    
def is_jav(title):
    if not title:
        return None
    if title.endswith('/'):
        title = title[:-1]
    else:
        title = title[title.rfind('/')+1:]
    title = title.upper().replace("SIS001", "").replace("1080P", "").replace("720P", "").replace("2160P", "")
    t = re.search(r'T28[\-_]\d{3,4}', title)
    # 一本道
    if not t:
        t = re.search(r'1PONDO[\-_]\d{6}[\-_]\d{2,4}', title)
        if t:
            t = t.group().replace("1PONDO_", "").replace("1PONDO-", "")
    if not t:
        t = re.search(r'HEYZO[\-_]?\d{4}', title)
    if not t:
        # 加勒比
        t = re.search(r'CARIB[\-_]\d{6}[\-_]\d{3}' ,title)
        if t:
            t = t.group().replace("CARIB-", "").replace("CARIB_", "")
    if not t:
        # 东京热
        t = re.search(r'N[-_]\d{4}' ,title)
    
    if not t:
        # Jukujo-Club | 熟女俱乐部
        t = re.search(r'JUKUJO[-_]\d{4}' ,title)

    # 通用
    # if not t:
    #     t = re.search(r'S[A-Z]{1,4}[-_]\d{3,5}' ,title)
    if not t:
        t = re.search(r'[A-Z]{2,5}[-_]\d{3,5}' ,title)
    if not t:
        t = re.search(r'\d{6}[\-_]\d{2,4}' ,title)

    # if not t:
    #     t = re.search(r'[A-Z]+\d{3,5}' ,title)
    
    # if not t:
    #     t = re.search(r'[A-Za-z]+[-_]?\d+' ,title)
    
    # if not t:
    #     t = re.search(r'\d+[-_]?\d+' ,title)
        
    if not t:
        return None
    else:
        t = t.group().replace("_", "-")
        return t