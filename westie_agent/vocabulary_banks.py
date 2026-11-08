"""Small, original practice selections; these are not official IELTS/AWL lists.

Keep legacy cards intact so existing progress and other vocabulary tools work.
"""
from dataclasses import dataclass

from .vocabulary_words import WORDS


@dataclass(frozen=True)
class WordBank:
    key: str
    title: str
    description: str
    words: tuple


_TOPICS = {
    'listening': (
        '雅思 · 听力场景', '住宿、出行、预约与校园生活。', [
            ('accommodation', '住宿', 'The university offers accommodation near the library.'),
            ('reservation', '预订', 'I would like to confirm my reservation for Friday.'),
            ('itinerary', '行程', 'Please check the itinerary before we leave.'),
            ('departure', '出发；离开', 'The departure time is printed on your ticket.'),
            ('destination', '目的地', 'Our final destination is a small coastal town.'),
            ('fare', '车费；票价', 'The bus fare includes a return journey.'),
            ('platform', '站台', 'The train to Oxford leaves from platform three.'),
            ('luggage', '行李', 'You can leave your luggage at reception.'),
            ('reception', '接待处', 'Ask at reception for a map of the campus.'),
            ('deposit', '押金', 'A small deposit is required to reserve the room.'),
            ('refund', '退款', 'You can request a refund if the tour is cancelled.'),
            ('receipt', '收据', 'Keep your receipt until the end of the course.'),
            ('appointment', '预约', 'My appointment with the adviser is at ten.'),
            ('enrolment', '注册；入学登记', 'Enrolment takes place in the main hall.'),
            ('timetable', '时间表', 'The new timetable is available on the noticeboard.'),
            ('deadline', '截止日期', 'The deadline for the application is Monday.'),
            ('orientation', '迎新介绍；情况介绍', 'New students can join an orientation session.'),
            ('seminar', '研讨课', 'The seminar will be held in room twelve.'),
            ('lecture', '讲座；讲课', 'The lecture begins immediately after lunch.'),
            ('laboratory', '实验室', 'Safety glasses must be worn in the laboratory.'),
            ('canteen', '食堂', 'The canteen serves hot meals until two.'),
            ('membership', '会员资格', 'Student membership includes access to the pool.'),
            ('insurance', '保险', 'Travel insurance is recommended for this trip.'),
            ('brochure', '宣传册', 'The brochure lists all the available courses.'),
        ]),
    'speaking': (
        '雅思 · 口语表达', '描述经历、兴趣、人物与日常选择。', [
            ('hometown', '家乡', 'My hometown is known for its peaceful parks.'),
            ('neighbourhood', '街区；社区', 'There is a small bakery in my neighbourhood.'),
            ('leisure', '闲暇', 'I enjoy reading in my leisure time.'),
            ('memorable', '难忘的', 'It was a memorable trip with my closest friends.'),
            ('fascinating', '极有趣的；吸引人的', 'I find the history of this city fascinating.'),
            ('rewarding', '有收获的；值得做的', 'Teaching children can be very rewarding.'),
            ('convenient', '方便的', 'Living near a station is convenient for me.'),
            ('affordable', '负担得起的', 'The cafe offers affordable meals for students.'),
            ('crowded', '拥挤的', 'The market is usually crowded at weekends.'),
            ('peaceful', '宁静的', 'I prefer a peaceful place when I need to study.'),
            ('enthusiastic', '热情的', 'My teacher is enthusiastic about local history.'),
            ('generous', '慷慨的', 'She is generous with her time and advice.'),
            ('patient', '有耐心的', 'My grandfather was patient when he taught me chess.'),
            ('confident', '自信的', 'Practice helped me feel more confident in class.'),
            ('ambitious', '有抱负的', 'My sister is ambitious and hopes to run a business.'),
            ('curious', '好奇的', 'I have always been curious about how machines work.'),
            ('relaxing', '令人放松的', 'Gardening is a relaxing activity after work.'),
            ('routine', '日常惯例', 'A short walk is part of my morning routine.'),
            ('commute', '通勤', 'I usually commute to work by train.'),
            ('occasion', '场合；时刻', 'We cook a special meal for the occasion.'),
            ('recommend', '推荐', 'I would recommend this museum to anyone visiting the city.'),
            ('prefer', '更喜欢', 'I prefer spending time outdoors with friends.'),
            ('appreciate', '欣赏；感激', 'I appreciate the support my family gives me.'),
            ('unwind', '放松', 'Listening to music helps me unwind in the evening.'),
        ]),
    'writing': (
        '雅思 · 写作表达', '图表变化、论证、社会与环境议题。', [
            ('proportion', '比例', 'The proportion of residents using public transport increased.'),
            ('percentage', '百分比', 'The percentage remained below twenty throughout the period.'),
            ('peak', '峰值', 'Demand reached a peak in the summer months.'),
            ('plateau', '进入平稳期', 'Sales began to plateau after a year of rapid growth.'),
            ('gradually', '逐渐地', 'The number of visitors rose gradually over the decade.'),
            ('sharply', '急剧地', 'House prices fell sharply during the final quarter.'),
            ('approximately', '大约', 'Approximately half of the respondents travelled by car.'),
            ('respectively', '分别地', 'The two groups contained ten and twelve students, respectively.'),
            ('nevertheless', '尽管如此；然而', 'The plan is expensive; nevertheless, it may save energy.'),
            ('furthermore', '此外；而且', 'The scheme reduces waste; furthermore, it creates local jobs.'),
            ('consequently', '因此；结果', 'Bus services improved; consequently, more people used them.'),
            ('arguably', '可以认为', 'Public libraries are arguably essential community spaces.'),
            ('urbanisation', '城市化', 'Rapid urbanisation can place pressure on public services.'),
            ('infrastructure', '基础设施', 'Investment in infrastructure can improve access to rural areas.'),
            ('inequality', '不平等', 'Access to education may help reduce income inequality.'),
            ('biodiversity', '生物多样性', 'Protecting wetlands helps preserve biodiversity.'),
            ('emissions', '排放物；排放量', 'Better insulation can reduce emissions from heating.'),
            ('renewable', '可再生的', 'The region is investing in renewable energy.'),
            ('legislation', '法律；法规', 'New legislation aims to improve road safety.'),
            ('subsidy', '补贴', 'A subsidy may make public transport more affordable.'),
            ('expenditure', '支出', 'Government expenditure on education rose during the period.'),
            ('drawback', '缺点；不利因素', 'One drawback of the proposal is its high initial cost.'),
            ('outweigh', '超过；重于', 'The long-term benefits may outweigh the initial costs.'),
            ('mitigate', '减轻；缓解', 'Planting trees can help mitigate the effects of urban heat.'),
        ]),
}


def _legacy(level):
    return tuple((w.term, w.meaning, w.example) for w in WORDS if w.level == level)


BANKS = (
    WordBank('ielts_core', '雅思 · 基础词汇', '常见主题与基础表达，适合起步。', _legacy('common')),
    WordBank('ielts_advanced', '雅思 · 进阶词汇', '阅读理解与较复杂的观点表达。', _legacy('advanced')),
    *(WordBank('ielts_' + key, title, description, tuple(rows))
      for key, (title, description, rows) in _TOPICS.items()),
    WordBank('academic', '学术英语 · 论文与研究', '摘要、研究方法、证据与结果讨论。', _legacy('academic')),
)
BANK_BY_ID = {bank.key: bank for bank in BANKS}
PRESET_WORDS = tuple(dict((row[0], row) for bank in BANKS for row in bank.words).values())
