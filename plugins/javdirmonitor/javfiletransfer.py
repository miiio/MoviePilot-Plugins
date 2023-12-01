import re
from pathlib import Path
from threading import Lock
from typing import Optional, List, Tuple, Union, Dict

from jinja2 import Template

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfo, MetaInfoPath
from app.log import logger
from app.modules import _ModuleBase
from app.schemas import TransferInfo, ExistMediaInfo, TmdbEpisode
from app.schemas.types import MediaType
from app.utils.string import StringUtils
from app.utils.system import SystemUtils

lock = Lock()


class JavFileTransferModule(_ModuleBase):
    
    def init_module(self) -> None:
        pass

    def stop(self):
        pass

    def init_setting(self) -> Tuple[str, Union[str, bool]]:
        pass

    def transfer(self, path: Path, meta: MetaBase, mediainfo: MediaInfo,
                 transfer_type: str, target: Path = None) -> TransferInfo:
        """
        文件转移
        :param path:  文件路径
        :param meta: 预识别的元数据，仅单文件转移时传递
        :param mediainfo:  识别的媒体信息
        :param transfer_type:  转移方式
        :param target:  目标路径
        :return: {path, target_path, message}
        """
        if not target:
            logger.error("未找到媒体库目录，无法转移文件")
            return TransferInfo(success=False,
                                path=path,
                                message="未找到媒体库目录")
        else:
            logger.info(f"获取转移目标路径：{target}")
        # 转移
        return self.transfer_media(in_path=path,
                                   in_meta=meta,
                                   mediainfo=mediainfo,
                                   transfer_type=transfer_type,
                                   target_dir=target)
    
    def transfer_media(self,
                       in_path: Path,
                       in_meta: MetaBase,
                       mediainfo: MediaInfo,
                       transfer_type: str,
                       target_dir: Path
                       ) -> TransferInfo:
        """
        识别并转移一个文件或者一个目录下的所有文件
        :param in_path: 转移的路径，可能是一个文件也可以是一个目录
        :param in_meta：预识别元数据
        :param mediainfo: 媒体信息
        :param target_dir: 媒体库根目录
        :param transfer_type: 文件转移方式
        :return: TransferInfo、错误信息
        """
        # 检查目录路径
        if not in_path.exists():
            return TransferInfo(success=False,
                                path=in_path,
                                message=f"{in_path} 路径不存在")

        if transfer_type not in ['rclone_copy', 'rclone_move']:
            # 检查目标路径
            if not target_dir.exists():
                return TransferInfo(success=False,
                                    path=in_path,
                                    message=f"{target_dir} 目标路径不存在")

        # 媒体库目的目录
        target_dir = self.__get_dest_dir(mediainfo=mediainfo, target_dir=target_dir)

        # 重命名格式
        # rename_format = settings.TV_RENAME_FORMAT \
        #     if mediainfo.type == MediaType.TV else settings.MOVIE_RENAME_FORMAT

        rename_format = "{{year}} {{title}}" \
                        "/{{year}} {{title}}" \
                        "{{fileExt}}"

        # 判断是否为文件夹
        if in_path.is_dir():
            # 转移整个目录
            # 是否蓝光原盘
            bluray_flag = SystemUtils.is_bluray_dir(in_path)
            if bluray_flag:
                logger.info(f"{in_path} 是蓝光原盘文件夹")
            # 原文件大小
            file_size = in_path.stat().st_size
            # 目的路径
            new_path = self.get_rename_path(
                path=target_dir,
                template_string=rename_format,
                rename_dict=self.__get_naming_dict(meta=in_meta,
                                                   mediainfo=mediainfo)
            ).parent
            # 转移蓝光原盘
            retcode = self.__transfer_dir(file_path=in_path,
                                          new_path=new_path,
                                          transfer_type=transfer_type)
            if retcode != 0:
                logger.error(f"文件夹 {in_path} 转移失败，错误码：{retcode}")
                return TransferInfo(success=False,
                                    message=f"错误码：{retcode}",
                                    path=in_path,
                                    target_path=new_path,
                                    is_bluray=bluray_flag)

            logger.info(f"文件夹 {in_path} 转移成功")
            # 返回转移后的路径
            return TransferInfo(success=True,
                                path=in_path,
                                target_path=new_path,
                                total_size=file_size,
                                is_bluray=bluray_flag)
        else:
            # 目的文件名
            new_file = self.get_rename_path(
                path=target_dir,
                template_string=rename_format,
                rename_dict=self.__get_naming_dict(
                    meta=in_meta,
                    mediainfo=mediainfo,
                    file_ext=in_path.suffix
                )
            )

            # 判断是否要覆盖
            overflag = False
            if new_file.exists():
                # 目标文件已存在
                OVERWRITE_MODE = 'size'
                logger.info(f"目标文件已存在，转移覆盖模式：{OVERWRITE_MODE}")
                match OVERWRITE_MODE:
                    case 'always':
                        # 总是覆盖同名文件
                        overflag = True
                    case 'size':
                        # 存在时大覆盖小
                        if new_file.stat().st_size < in_path.stat().st_size:
                            logger.info(f"目标文件文件大小更小，将被覆盖：{new_file}")
                            overflag = True
                        else:
                            return TransferInfo(success=False,
                                                message=f"媒体库中已存在，且质量更好",
                                                path=in_path,
                                                target_path=new_file,
                                                fail_list=[str(in_path)])
                    case 'never':
                        # 存在不覆盖
                        return TransferInfo(success=False,
                                            message=f"媒体库中已存在，当前设置为不覆盖",
                                            path=in_path,
                                            target_path=new_file,
                                            fail_list=[str(in_path)])
                    # case 'latest':
                    #     # 仅保留最新版本
                    #     self.delete_all_version_files(new_file)
                    #     overflag = True
                    case _:
                        pass
            # 原文件大小
            file_size = in_path.stat().st_size
            # 转移文件
            retcode = self.__transfer_file(file_item=in_path,
                                           new_file=new_file,
                                           transfer_type=transfer_type,
                                           over_flag=overflag)
            if retcode != 0:
                logger.error(f"文件 {in_path} 转移失败，错误码：{retcode}")
                return TransferInfo(success=False,
                                    message=f"错误码：{retcode}",
                                    path=in_path,
                                    target_path=new_file,
                                    fail_list=[str(in_path)])

            logger.info(f"文件 {in_path} 转移成功")
            return TransferInfo(success=True,
                                path=in_path,
                                target_path=new_file,
                                file_count=1,
                                total_size=file_size,
                                is_bluray=False,
                                file_list=[str(in_path)],
                                file_list_new=[str(new_file)])
        
        
    @staticmethod
    def __get_dest_dir(mediainfo: MediaInfo, target_dir: Path) -> Path:
        """
        根据设置并装媒体库目录
        :param mediainfo: 媒体信息
        :target_dir: 媒体库根目录
        """
        if mediainfo.type == "Jav":
            target_dir = target_dir / mediainfo.actors[0]['starName'] / mediainfo.douban_id
        
        if mediainfo.type == MediaType.MOVIE:
            # 电影
            if settings.LIBRARY_MOVIE_NAME:
                target_dir = target_dir / settings.LIBRARY_MOVIE_NAME / mediainfo.category
            else:
                # 目的目录加上类型和二级分类
                target_dir = target_dir / mediainfo.type.value / mediainfo.category

        if mediainfo.type == MediaType.TV:
            # 电视剧
            if settings.LIBRARY_ANIME_NAME \
                    and mediainfo.genre_ids \
                    and set(mediainfo.genre_ids).intersection(set(settings.ANIME_GENREIDS)):
                # 动漫
                target_dir = target_dir / settings.LIBRARY_ANIME_NAME / mediainfo.category
            elif settings.LIBRARY_TV_NAME:
                # 电视剧
                target_dir = target_dir / settings.LIBRARY_TV_NAME / mediainfo.category
            else:
                # 目的目录加上类型和二级分类
                target_dir = target_dir / mediainfo.type.value / mediainfo.category
        return target_dir
    
    @staticmethod
    def get_rename_path(template_string: str, rename_dict: dict, path: Path = None) -> Path:
        """
        生成重命名后的完整路径
        """
        # 创建jinja2模板对象
        template = Template(template_string)
        # 渲染生成的字符串
        render_str = template.render(rename_dict)
        # 目的路径
        if path:
            return path / render_str
        else:
            return Path(render_str)
        
    @staticmethod
    def __get_naming_dict(meta: MetaBase, mediainfo: MediaInfo, file_ext: str = None) -> dict:
        """
        根据媒体信息，返回Format字典
        :param meta: 文件元数据
        :param mediainfo: 识别的媒体信息
        :param file_ext: 文件扩展名
        :param episodes_info: 当前季的全部集信息
        """
        return {
            # 番号
            "code": mediainfo.douban_id,
            # 时间
            "year": mediainfo.year,
            # 标题
            "title": mediainfo.title if len(mediainfo.title) <= 48 else (mediainfo.title[:48] + "……"),
            # 主演
            "actor": mediainfo.actors[0]['starName'],
            # 演员
            "actors": ",".join([actor['starName'] for actor in mediainfo.actors]),
            "producer": mediainfo.producer['producerName'],
            "publisher": mediainfo.publisher['publisherName'],
            # 文件后缀
            "fileExt": file_ext,
        }
    
    def __transfer_dir(self, file_path: Path, new_path: Path, transfer_type: str) -> int:
        """
        转移整个文件夹
        :param file_path: 原路径
        :param new_path: 新路径
        :param transfer_type: RmtMode转移方式
        """
        logger.info(f"正在{transfer_type}目录：{file_path} 到 {new_path}")
        # 复制
        retcode = self.__transfer_dir_files(src_dir=file_path,
                                            target_dir=new_path,
                                            transfer_type=transfer_type)
        if retcode == 0:
            logger.info(f"文件 {file_path} {transfer_type}完成")
        else:
            logger.error(f"文件{file_path} {transfer_type}失败，错误码：{retcode}")

        return retcode
    
    def __transfer_dir_files(self, src_dir: Path, target_dir: Path, transfer_type: str) -> int:
        """
        按目录结构转移目录下所有文件
        :param src_dir: 原路径
        :param target_dir: 新路径
        :param transfer_type: RmtMode转移方式
        """
        retcode = 0
        for file in src_dir.glob("**/*"):
            # 过滤掉目录
            if file.is_dir():
                continue
            # 使用target_dir的父目录作为新的父目录
            new_file = target_dir.joinpath(file.relative_to(src_dir))
            if new_file.exists():
                logger.warn(f"{new_file} 文件已存在")
                continue
            if not new_file.parent.exists():
                new_file.parent.mkdir(parents=True, exist_ok=True)
            retcode = self.__transfer_command(file_item=file,
                                              target_file=new_file,
                                              transfer_type=transfer_type)
            if retcode != 0:
                break

        return retcode
    
    @staticmethod
    def __transfer_command(file_item: Path, target_file: Path, transfer_type: str) -> int:
        """
        使用系统命令处理单个文件
        :param file_item: 文件路径
        :param target_file: 目标文件路径
        :param transfer_type: RmtMode转移方式
        """
        with lock:

            # 转移
            if transfer_type == 'link':
                # 硬链接
                retcode, retmsg = SystemUtils.link(file_item, target_file)
            elif transfer_type == 'softlink':
                # 软链接
                retcode, retmsg = SystemUtils.softlink(file_item, target_file)
            elif transfer_type == 'move':
                # 移动
                retcode, retmsg = SystemUtils.move(file_item, target_file)
            elif transfer_type == 'rclone_move':
                # Rclone 移动
                retcode, retmsg = SystemUtils.rclone_move(file_item, target_file)
            elif transfer_type == 'rclone_copy':
                # Rclone 复制
                retcode, retmsg = SystemUtils.rclone_copy(file_item, target_file)
            else:
                # 复制
                retcode, retmsg = SystemUtils.copy(file_item, target_file)

        if retcode != 0:
            logger.error(retmsg)

        return retcode
    
    def __transfer_file(self, file_item: Path, new_file: Path, transfer_type: str,
                        over_flag: bool = False) -> int:
        """
        转移一个文件，同时处理其他相关文件
        :param file_item: 原文件路径
        :param new_file: 新文件路径
        :param transfer_type: RmtMode转移方式
        :param over_flag: 是否覆盖，为True时会先删除再转移
        """
        if new_file.exists():
            if not over_flag:
                logger.warn(f"文件已存在：{new_file}")
                return 0
            else:
                logger.info(f"正在删除已存在的文件：{new_file}")
                new_file.unlink()
        logger.info(f"正在转移文件：{file_item} 到 {new_file}")
        # 创建父目录
        new_file.parent.mkdir(parents=True, exist_ok=True)
        retcode = self.__transfer_command(file_item=file_item,
                                          target_file=new_file,
                                          transfer_type=transfer_type)
        if retcode == 0:
            logger.info(f"文件 {file_item} {transfer_type}完成")
        else:
            logger.error(f"文件 {file_item} {transfer_type}失败，错误码：{retcode}")
            return retcode
        # 处理其他相关文件
        return 0