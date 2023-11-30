from app.utils.singleton import Singleton
from app.core.config import settings
from app.utils.http import RequestUtils
from lxml import etree
import re
import requests

class JavbusWeb(object, metaclass=Singleton):
    _session = requests.Session()

    _web_base = "https://www.javbus.com"
    global _javbus_web_base
    _javbus_web_base = _web_base
    _proxies = settings.PROXY
    
    _page_limit = 50
    _timout = 5

    _weburls = {
        # 关键字搜索(有码)
        "search": f"{_web_base}/%s/%s/%s&type=1",
        
        # 影片详情
        "detail": f"{_web_base}/%s",
        
        # 磁力
        "magnets": f"{_web_base}/ajax/uncledatoolsbyajax.php?lang=zh&gid=%s&uc=%s",
        
        # 演员参与作品
        "actor_medias": f"{_web_base}/star/%s/%s"
    }

    _webparsers = {
        "search_pagination": {
            "list": '//ul[@class="pagination pagination-lg"]',
            "item": {
                "currentPage": "./li[@class='active']/a/text()",
                "hasNextPage": ["./li[@class='active']/following-sibling::li", False],
                "nextPage": ["./li[@class='active']/following-sibling::li[1]/a/text()", -1],
                "pages": ["./li/a[not(@id='next') and not(@id='pre')]/text()", []]
            },
            "format": {
                "hasNextPage": lambda x : True if x else None,
                "currentPage": lambda x : int(x) if x else 1,
                "nextPage": lambda x : int(x) if x else 1,
            }
        },
        "search_movies": {
            "list": '//a[@class="movie-box"]',
            "item": {
                "date": './div[@class="photo-info"]/span/date[2]/text()',
                "id": './div[@class="photo-info"]/span/date[1]/text()',
                "img": './div[@class="photo-frame"]/img/@src',
                "title": './div[@class="photo-frame"]/img/@title',
                "tags": ['./div[@class="photo-info"]/span/div[@class="item-tag"]/button/text()', []],
            },
            "format": {
                "img": lambda x : None if not x else _javbus_web_base + x
            }
        },
        "detail_info": {
            "id": '//span[text()="識別碼:"]/following-sibling::span[1]/text()',
            "title": '//div[@class="container"]/h3/text()',
            'img': ['//a[@class="bigImage"]/@href', lambda x : None if not x else _javbus_web_base + x],
            "date": ['//span[text()="發行日期:"]/following-sibling::text()', lambda x:None if not x else x.strip()],
            "videoLength": ['//span[text()="長度:"]/following-sibling::text()', lambda x:None if not x else x.replace('分鐘','').strip()],
        },
        "directorInfo": {
            "directorId": ['//span[text()="導演:"]/following-sibling::a[1]/@href', lambda a:None if not a else a[a.rfind('/')+1:]],
            "directorName": '//span[text()="導演:"]/following-sibling::a[1]/text()',
        },
        "producerInfo": {
            "producerId": ['//span[text()="製作商:"]/following-sibling::a[1]/@href', lambda a:None if not a else a[a.rfind('/')+1:]],
            "producerName": '//span[text()="製作商:"]/following-sibling::a[1]/text()',
        },
        "publisherInfo": {
            "publisherId": ['//span[text()="發行商:"]/following-sibling::a[1]/@href', lambda a:None if not a else a[a.rfind('/')+1:]],
            "publisherName": '//span[text()="發行商:"]/following-sibling::a[1]/text()',
        },
        "seriesInfo": {
            "seriesId": ['//span[text()="系列:"]/following-sibling::a[1]/@href', lambda a:None if not a else a[a.rfind('/')+1:]],
            "seriesName": '//span[text()="系列:"]/following-sibling::a[1]/text()',
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
            "list": '//p[@class="star-show"]/following-sibling::p[1]/span/a',
            "item": {
                "starId": ['./@href', None],
                "starName":['./text()', ''],
            },
            "format": {
                "starId": lambda a:None if not a else a[a.rfind('/')+1:],
            }
        },
        "samples": {
            "list": '//a[@class="sample-box"]',
            "item": {
                "alt": './div/img/@title',
                "id": './div/img/@src',
                "src": './@href',
                "thumbnail": './div/img/@src',
            },
            "format": {
                "id": lambda a:None if not a else a[a.rfind('/')+1:a.find('.')],
            }
        },
        'magnets': {
            "list": '//tr',
            "item": {
                "link": ['./td[1]/a/@href', ''],
                "title": ['./td[1]/a/text()', ''],
                "size": ['./td[2]/a/text()', ''],
                "shareDate": ['./td[3]/a/text()', ''],
            },
            "format": {
                "title": lambda x:None if not x else " ".join([item.strip() for item in x]),
                "size": lambda x:None if not x else x.strip(),
                "shareDate": lambda x:None if not x else x.strip(),
            }
        },
        'related': {
            'list': '//div[@id="related-waterfall"]/a[@class="movie-box"]',
            'item': {
                'url': './@href',
                'id': './@href',
                'img': './div[@class="photo-frame"]/img/@src',
                'title': './div[@class="photo-info"]/span/text()',
            },
            'format': {
                'id': lambda a:None if not a else a[a.rfind('/')+1:],
                "img": lambda x : None if not x else _javbus_web_base + x
            }
        }
    }

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
    def __invoke_json(cls, url, *kwargs):
        req_url = cls._jsonurls.get(url)
        if not req_url:
            return None
        req = RequestUtils(session=cls._session,
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
                pass
                # ExceptionUtils.exception_traceback(e)
        return obj

    @classmethod
    def search(cls, keyword, page=1, magnet='all', type='normal'):
        """
        关键字查询
        """
        doc = cls.__invoke_web("search", cookies="existmag={}".format('mag' if magnet=='all' else 'all'), params=('search' if type=='normal' else 'uncensored', keyword, page))
        movies = cls.__get_list("search_movies", doc)
        pagination = cls.__get_list('search_pagination', doc)
        return {'movies': movies if movies else [], 'pagination': pagination, "keyword": keyword}
    
    
    @classmethod
    def detail(cls, id):
        """
        影片详情
        """
        doc = cls.__invoke_web("detail", params=(id))
        info = cls.__get_obj("detail_info", doc)
        info['score'] = 0.0
        info['director'] = cls.__get_obj("directorInfo", doc)
        info['producer'] = cls.__get_obj("producerInfo", doc)
        info['publisher'] = cls.__get_obj("publisherInfo", doc)
        info['series'] = cls.__get_obj("seriesInfo", doc)
        info['tags'] = cls.__get_list("tags", doc)
        info['stars'] = cls.__get_list("stars", doc)
        info['samples'] = cls.__get_list("samples", doc)
        info['related'] = cls.__get_list("related", doc)
        gidReg = "var gid = (\d+);"
        ucReg = "var uc = (\d+);"
        gid = re.search(gidReg, doc)
        gid = gid.group(1) if gid else None
        
        uc = re.search(ucReg, doc)
        uc = uc.group(1) if uc else None
        
        magnets_html = cls.__invoke_web("magnets", headers={'referer': cls._web_base+'/'+id}, params=(gid,uc))
        magnets = cls.__get_list("magnets", magnets_html)
        for magnet in magnets:
            id_res = re.search('magnet:\?xt=urn:btih:(\w+)', magnet['link'])
            magnet['id'] = id_res.group(1) if id_res else ""
            magnet['isHD'] = '高清' in magnet['title']
            magnet['hasSubtitle'] = '字幕' in magnet['title'] or '-c' in magnet['title'] or '-C' in magnet['title']
            magnet['numberSize'] = cls.__bytes(size=magnet['size'])
            
        magnets = filter(lambda x: x['id'] and x['link'] and x['title'] and x['numberSize']>0, magnets)
        magnets = sorted(magnets, key=lambda x:x['numberSize'], reverse=True)
        info['magnets'] = magnets
        return info
    
    
    @classmethod
    def actor_medias(cls, aid, page=1, magnet='all', type='normal'):
        """
        演员参与作品
        """
        doc = cls.__invoke_web("actor_medias", cookies="existmag={}".format('mag' if magnet=='all' else 'all'), params=(aid, page))
        movies = cls.__get_list("search_movies", doc)
        pagination = cls.__get_list('search_pagination', doc)
        return {'movies': movies if movies else [], 'pagination': pagination, "actor_id": aid}
        
        
        
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
