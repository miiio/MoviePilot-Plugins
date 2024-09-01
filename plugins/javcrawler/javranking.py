class JavRanking:
    def __init__(self, av_number, av_title, av_cover, release_date, is_downloadable, has_subtitle, ranking_type, ranking_date, ranking, data_source, has_code, retrieval_time):
        self.id = None
        self.av_number = av_number
        self.av_title = av_title
        self.av_cover = av_cover
        self.release_date = release_date
        self.is_downloadable = is_downloadable
        self.has_subtitle = has_subtitle
        self.ranking_type = ranking_type
        self.ranking_date = ranking_date
        self.ranking = ranking
        self.data_source = data_source
        self.has_code = has_code
        self.retrieval_time = retrieval_time

    def __str__(self):
        return f"JavRanking(id={self.id}, av_number={self.av_number}, av_title={self.av_title}, av_cover_image={self.av_cover}, release_date={self.release_date}, is_downloadable={self.is_downloadable}, has_subtitle={self.has_subtitle}, ranking_type={self.ranking_type}, ranking_date={self.ranking_date}, ranking={self.ranking}, data_source={self.data_source}, has_code={self.has_code}, retrieval_time={self.retrieval_time})"