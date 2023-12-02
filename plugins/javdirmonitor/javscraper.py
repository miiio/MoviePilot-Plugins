import time
import os
from pathlib import Path
from typing import Union
from xml.dom import minidom

from requests import RequestException

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfo
from app.log import logger
from app.schemas.types import MediaType
from app.utils.common import retry
from app.utils.dom import DomUtils
from app.utils.http import RequestUtils
from app.utils.system import SystemUtils
from PIL import Image

class JavScraper:
    _transfer_type = "link"
    def __init__(self):
        pass

    def scrape_metadata(self, path: Path, mediainfo: MediaInfo, transfer_type: str) -> None:
        """
        刮削元数据
        :param path: 媒体文件路径
        :param mediainfo:  识别的媒体信息
        :param transfer_type:  转移类型
        :return: 成功或失败
        """
        if path.is_file():
            # 单个文件
            logger.info(f"开始刮削媒体库文件：{path} ...")
            self.gen_scraper_files(mediainfo=mediainfo,
                                           file_path=path,
                                           transfer_type=transfer_type)
        else:
            # 目录下的所有文件
            logger.info(f"开始刮削目录：{path} ...")
            for file in SystemUtils.list_files(path, settings.RMT_MEDIAEXT):
                if not file:
                    continue
                self.gen_scraper_files(mediainfo=mediainfo,
                                               file_path=file,
                                               transfer_type=transfer_type)
        logger.info(f"{path} 刮削完成")

    def gen_scraper_files(self, mediainfo: MediaInfo, file_path: Path, transfer_type: str):
        """
        生成刮削文件，包括NFO和图片，传入路径为文件路径
        :param mediainfo: 媒体信息
        :param file_path: 文件路径或者目录路径
        :param transfer_type: 传输类型
        """
        self._transfer_type = transfer_type
        # 不已存在时才处理
        if not file_path.with_name("movie.nfo").exists() \
                and not file_path.with_suffix(".nfo").exists():
            #  生成电影描述文件
            self.__gen_movie_nfo_file(mediainfo=mediainfo,
                                        file_path=file_path)
        # 生成电影图片
        for attr_name, attr_value in vars(mediainfo).items():
            if attr_value \
                    and attr_name.endswith("_path") \
                    and attr_value \
                    and isinstance(attr_value, str) \
                    and attr_value.startswith("http"):
                image_name = attr_name.replace("_path", "") + Path(attr_value).suffix
                if "sample" not in image_name:
                    self.__save_image(url=attr_value,
                                        file_path=file_path.with_name(image_name),
                                        is_poster=attr_name=='poster_path', badge=mediainfo.cn_subtitle)
                else:
                    sample_dir = file_path.parent.joinpath("extrafanart")
                    if not sample_dir.exists():
                        sample_dir.mkdir()
                    image_path = sample_dir.joinpath(image_name)
                    self.__save_image(url=attr_value,
                                        file_path=image_path,
                                        is_poster=False, badge=False)
    def __gen_movie_nfo_file(self,
                             mediainfo: MediaInfo,
                             file_path: Path):
        """
        生成电影的NFO描述文件
        :param mediainfo: 识别后的媒体信息
        :param file_path: 电影文件路径
        """
        # 开始生成XML
        logger.info(f"正在生成Jav NFO文件：{file_path.name}")
        doc = minidom.Document()
        root = DomUtils.add_node(doc, doc, "movie")
        # 公共部分
        doc = self.__gen_common_nfo(mediainfo=mediainfo,
                                    doc=doc,
                                    root=root)
        # 标题
        DomUtils.add_node(doc, root, "title", mediainfo.title or "")
        DomUtils.add_node(doc, root, "originaltitle", mediainfo.original_title or "")
        # 发布日期
        DomUtils.add_node(doc, root, "premiered", mediainfo.release_date or "")
        # 年份
        DomUtils.add_node(doc, root, "year", mediainfo.year or "")
        # 保存
        self.__save_nfo(doc, file_path.with_suffix(".nfo"))


    @staticmethod
    def __gen_common_nfo(mediainfo: MediaInfo, doc, root):
        """
        生成公共NFO
        """
        # 添加时间
        DomUtils.add_node(doc, root, "dateadded",
                          time.strftime('%Y-%m-%d %H:%M:%S',
                                        time.localtime(time.time())))
        # javid
        DomUtils.add_node(doc, root, "javid", mediainfo.douban_id or "")
        uniqueid_tmdb = DomUtils.add_node(doc, root, "uniqueid", mediainfo.douban_id or "")
        uniqueid_tmdb.setAttribute("type", "javid")
        uniqueid_tmdb.setAttribute("default", "true")

        # 简介
        xplot = DomUtils.add_node(doc, root, "plot")
        xplot.appendChild(doc.createCDATASection(mediainfo.title or ""))

        # 导演
        for director in mediainfo.directors:
            if director:
                DomUtils.add_node(doc, root, "director", director.get("directorName") or "")

        # 演员
        for actor in mediainfo.actors:
            if actor.get('name', None) is None: continue
            # 获取中文名
            xactor = DomUtils.add_node(doc, root, "actor")
            DomUtils.add_node(doc, xactor, "name", actor.get("starName") or "")
            DomUtils.add_node(doc, xactor, "javbus_id", actor.get("starId") or "")
            DomUtils.add_node(doc, xactor, "thumb",
                              f"https://www.javbus.com/pics/actress/{actor.get('starId')}_a.jpg")
            # https://www.javbus.com/pics/actress/okq_a.jpg

        # 风格
        genres = mediainfo.genres or []
        for genre in genres:
            DomUtils.add_node(doc, root, "genre", genre.get("tagName") or "")
        # 评分
        DomUtils.add_node(doc, root, "rating", mediainfo.vote_average or "0")

        return doc
    
    def __save_nfo(self, doc, file_path: Path):
        """
        保存NFO
        """
        if file_path.exists():
            return
        xml_str = doc.toprettyxml(indent="  ", encoding="utf-8")
        if self._transfer_type in ['rclone_move', 'rclone_copy']:
            self.__save_remove_file(file_path, xml_str)
        else:
            file_path.write_bytes(xml_str)
        logger.info(f"NFO文件已保存：{file_path}")
        
    @retry(RequestException, logger=logger)
    def __save_image(self, url: str, file_path: Path, is_poster: bool, badge: bool):
        """
        下载图片并保存
        """
        if file_path.exists():
            return
        try:
            logger.info(f"正在下载{file_path.stem}图片：{url} ...")
            r = RequestUtils().get_res(url=url, raise_exception=True)
            if r:
                if self._transfer_type in ['rclone_move', 'rclone_copy']:
                    self.__save_remove_file(file_path, r.content)
                else:
                    if not is_poster:
                        file_path.write_bytes(r.content)
                    else:
                        file_path.write_bytes(r.content)
                        img = Image.open(file_path)
                        w, h = img.size
                        img = img.crop((w - h * 0.7, 0, w, h))
                        if badge:
                            badge_path = Path(os.path.join(os.path.abspath(os.path.dirname(__file__)), "zimu.png"))
                            if not badge_path.exists():
                                badge_path.write_bytes(RequestUtils().get_res(url="https://oss-game88.oss-cn-beijing.aliyuncs.com/js_plugs/album/202210/zimu.png", raise_exception=True))
                            badge_img = Image.open(str(badge_path))
                            w, h = img.size
                            badge_img = badge_img.resize((int(w*0.35), int(w*0.35)))
                            img.paste(badge, (0,0), badge)
                        img.save(file_path)
                logger.info(f"图片已保存：{file_path}")
                time.sleep(0.5)
            else:
                logger.info(f"{file_path.stem}图片下载失败，请检查网络连通性")
        except RequestException as err:
            raise err
        except Exception as err:
            logger.error(f"{file_path.stem}图片下载失败：{str(err)}")

            
    def __save_remove_file(self, out_file: Path, content: Union[str, bytes]):
        """
        保存文件到远端
        """
        temp_file = settings.TEMP_PATH / str(out_file)[1:]
        temp_file_dir = temp_file.parent
        if not temp_file_dir.exists():
            temp_file_dir.mkdir(parents=True, exist_ok=True)
        temp_file.write_bytes(content)
        if self._transfer_type == 'rclone_move':
            SystemUtils.rclone_move(temp_file, out_file)
        elif self._transfer_type == 'rclone_copy':
            SystemUtils.rclone_copy(temp_file, out_file)