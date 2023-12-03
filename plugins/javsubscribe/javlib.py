from app.utils.singleton import Singleton
from app.core.config import settings
from app.utils.http import RequestUtils
from lxml import etree
import re
import requests

class JavlibWeb(object):
    _session = requests.Session()

    _web_base = "https://www.javlibrary.com/cn"
    _page_limit = 50
    _timout = 5
    _proxies = settings.PROXY
    _user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36'

    _weburls = {
        # 最想要
        "mostwanted": f"{_web_base}/vl_mostwanted.php?page=%s&mode=%s&",
        # 评价最高
        "bestrated": f"{_web_base}/vl_bestrated.php?page=%s&mode=%s&",
        # 新发行
        "newrelease": f"{_web_base}/vl_newrelease.php?page=%s&mode=%s&",
        # 新加入
        "newentries": f"{_web_base}/vl_newentries.php?page=%s&mode=%s&",
        # 搜索
        "search": f"{_web_base}/vl_searchbyid.php?keyword=%s&page=%s&mode=%s&",
        # 详情
        "detail_info": f"{_web_base}/?v=%s",
    }

    _webparsers = {
        "common_list": {
            "list": '//*[@class="video"]',
            "item": {
                "title": "./a/div[2]/text()",
                "cover": "./a/img/@onerror",
                "code": "./a/div[1]/text()",
                "vid": "./@id",
            },
            "format": {
                "cover": lambda x : x.replace("ThumbError(this, '",'').replace("');",''),
                "vid": lambda x : x.replace("vid_", ""),
            }
        },
        "detail_info": {
            "id": '//td[text()="识别码:"]/following-sibling::td[1]/text()',
            "title": '//h3[@class="post-title text"]/a/text()',
            'img': ['//img[@id="video_jacket_img"]/@src', lambda x : x if x.startswith('https:') else "https:" + x],
            "date": ['//td[text()="发行日期:"]/following-sibling::td/text()', lambda x:None if not x else x.strip()],
            "videoLength": '//td[text()="长度:"]/following-sibling::td/span/text()',
            "rating": ['//span[@class="score"]/text()', lambda x : 0 if not x else eval(x) ],
            'vid': ['//h3[@class="post-title text"]/a/@href', lambda a:None if not a else a[a.rfind('=')+1:]],
        },
        "directorInfo": {
            "directorId": ['//td[text()="导演:"]/following-sibling::td/span/a/@href', lambda a:None if not a else a[a.rfind('=')+1:]],
            "directorName": ['//td[text()="导演:"]/following-sibling::td/span/a/text()',  lambda a: a if a else '----'],
        },
        "producerInfo": {
            "producerId": ['//td[text()="制作商:"]/following-sibling::td/span/a/@href', lambda a:None if not a else a[a.rfind('=')+1:]],
            "producerName": '//td[text()="制作商:"]/following-sibling::td/span/a/text()',
        },
        "publisherInfo": {
            "publisherId": ['//td[text()="发行商:"]/following-sibling::td/span/a/@href', lambda a:None if not a else a[a.rfind('=')+1:]],
            "publisherName": '//td[text()="发行商:"]/following-sibling::td/span/a/text()',
        },
        "tags": {
            "list": '//p[text()="類別:"]/following-sibling::p[1]/span/label/a',
            "item": {
                "tagId": ['./@href', None],
                "tagName":['./text()', ''],
            },
            "format": {
                "tagId": lambda a:None if not a else a[a.rfind('/')+1:],
            }
        },
        "stars": {
            "list": '//td[text()="类别:"]/following-sibling::td/span/a',
            "item": {
                "starId": ['./@href', None],
                "starName":['./text()', ''],
            },
            "format": {
                "starId": lambda a:None if not a else a[a.rfind('=')+1:],
            }
        }
    }
    
    @classmethod
    def detail_by_javid(cls, jav_id):
        """
        通过番号获取javlib影片信息
        """
        if not jav_id: return None
        jav_id = jav_id.upper()
        html = cls.__invoke_web("search", params=(jav_id, 1, 1))
        list = cls.__get_list("common_list", html)
        if list and len(list) > 0:
            # 有多个结果，筛选第一个符合条件的
            for item in list:
                if jav_id == item['code'] or jav_id in item['code']:
                    return cls.detail(vid = item['vid'])
            return None
        else:
            info = cls.__get_obj("detail_info", html)
            return info if info['id'] == jav_id or jav_id in info['id'] else None
    
    @classmethod
    def detail(cls, vid):
        """
        影片详情
        """
        doc = cls.__invoke_web("detail_info", params=(vid))
        info = cls.__get_obj("detail_info", doc)
        info['director'] = cls.__get_obj("directorInfo", doc)
        info['producer'] = cls.__get_obj("producerInfo", doc)
        info['publisher'] = cls.__get_obj("publisherInfo", doc)
        info['series'] = []
        info['tags'] = cls.__get_list("tags", doc)
        info['stars'] = cls.__get_list("stars", doc)
        info['samples'] = []
        return info
    
    
    @classmethod
    def search(cls, keyword, page=1, mode=1):
        """
        关键字查询
        """
        html = cls.__invoke_web("search", params=(keyword, page, mode))
        list = cls.__get_list("common_list", html)
        if list and len(list) > 0:
            return list
        else:
            info = cls.__get_obj("detail_info", html)
            return [{
                "title": info['title'],
                'cover': info['img'],
                'code': info['id'],
                'vid': info['vid'],
            }] if info else []
    
    
    @classmethod
    def mostwanted(cls, page=1, mode=1):
        """
        查询最想要
        """
        return cls.__get_list("common_list", cls.__invoke_web("mostwanted", params=(page, mode)))

    @classmethod
    def bestrated(cls, page=1, mode=1):
        """
        查询评价最高
        """
        return cls.__get_list("common_list", cls.__invoke_web("bestrated", params=(page, mode)))
    
    
    @classmethod
    def newrelease(cls, page=1, mode=1):
        """
        查询新发行
        """
        return cls.__get_list("common_list", cls.__invoke_web("newrelease", params=(page, mode)))
    
    
    @classmethod
    def newentries(cls, page=1, mode=1):
        """
        查询新加入
        """
        return cls.__get_list("common_list", cls.__invoke_web("newentries", params=(page, mode)))
    

    def __int__(self, cookie=None):
        pass

    @classmethod
    def __invoke_web(cls, url, params=(), cookies='', headers={}):
        req_url = cls._weburls.get(url)
        if not req_url:
            return None
        if "user-agent" not in headers:
            headers['user-agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36'
        return RequestUtils(cookies=cookies,
                            session=cls._session,
                            headers=headers,
                            proxies=cls._proxies,
                            timeout=cls._timout).get(url=req_url % params)
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
                if xpaths.get("format") and xpaths.get("format").get(key):
                    format = xpaths.get("format").get(key)
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
                # ExceptionUtils.exception_traceback(e)
                pass
        return obj