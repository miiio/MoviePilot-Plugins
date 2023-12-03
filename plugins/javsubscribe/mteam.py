from app.utils.singleton import Singleton
from app.core.config import settings
from app.utils.http import RequestUtils
from lxml import etree
import re
import requests
from app.db.models.site import Site
import requests

class MTeam(object):
    _session = requests.Session()

    _page_limit = 50
    _timout = 15
    _proxies = None
    # _proxies = settings.PROXY
    # _proxies = {
    #             "http": "http://192.168.1.10:7890",
    #             "https": "http://192.168.1.10:7890"
    #         }

    _weburls = {
    }

    _webparsers = {
        "search_list": {
            "list": '//table[@class="torrents"]/tr[1]/following-sibling::tr',
            "item": {
                "title": './td[@class="torrenttr"]/table/tr/td/a/@title',
                "desc": './td[@class="torrenttr"]/table/tr/td/a/b/text()',
                "size": './td[5]/text()',
                "seeds": './td[6]/b/a/text()',
                "downloads": './td[7]/b/a/text()',
                "completes": './td[8]/a/b/text()',
            },
            "format": {
                "size": lambda x: "".join(x)
            }
        },
    }

    _site = None
    mteam_url = "https://xp.m-team.io/"
    cookie = "tp=M2NlMzEwYTcyY2UxMDYwNWRiOWY3YjBhMTBjMmNiZTMzNTZlNjlmMw%3D%3D"
    def __init__(cls, site: Site) -> None:
        cls._site = site
        if site:
            cls.mteam_url = site.url
            cls.cookie = site.cookie
    
    @classmethod
    def __invoke_web(cls, url, cookies='', headers={}):
        payload={}
        response = requests.request("GET", cls.mteam_url, headers=headers, data=payload)
        headers = {
            'Host': 'xp.m-team.io',
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cookie': cookies
        }

        response = requests.request("GET", url, headers=headers, data=payload)
        return response.text


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
    def search_jav_list(cls, javlist):
        if not javlist or len(javlist) == 0: return []
        # https://xp.m-team.io/adult.php?incldead=1&spstate=0&inclbookmarked=0&search=ssis-809+ssis-666&search_area=0&search_mode=1
        query = "+".join([item.lower() for item in javlist])
        url = f"{cls.mteam_url}/adult.php?incldead=1&spstate=0&inclbookmarked=0&search=${query}&search_area=0&search_mode=1"
        doc = cls.__invoke_web(url, cookies=cls.cookie)
        print(doc)
        search_list = cls.__get_list("search_list", doc)
        return search_list
    
        
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