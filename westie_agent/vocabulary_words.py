"""Original starter cards for casual IELTS preparation, not an official wordlist.

Definitions cover the sense used in each original example, not every dictionary
sense. Keep the cards in a Python module so frozen builds include them offline.
"""
from dataclasses import dataclass


LEVELS = {"common": "常见词", "advanced": "进阶词", "academic": "学术词"}


@dataclass(frozen=True)
class Word:
    term: str
    level: str
    part: str
    meaning: str
    example: str
    translation: str


_ROWS = {
    "common": [
        ("retain", "v.", "保留；记住", "Short examples help me retain new words.", "简短的例句帮助我记住新词。"),
        ("adapt", "v.", "适应；调整", "Plants adapt to changes in their environment.", "植物会适应环境的变化。"),
        ("benefit", "n.", "益处；好处", "One benefit of walking is better sleep.", "散步的一个好处是睡得更好。"),
        ("approach", "n.", "方法；途径", "We tried a different approach to the problem.", "我们尝试了另一种解决这个问题的方法。"),
        ("available", "adj.", "可获得的；可使用的", "Quiet study rooms are available upstairs.", "楼上有可使用的安静自习室。"),
        ("balance", "n.", "平衡", "She found a balance between work and rest.", "她在工作与休息之间找到了平衡。"),
        ("challenge", "n.", "挑战；难题", "Finding clean water is a major challenge.", "找到干净的水是一项重大挑战。"),
        ("community", "n.", "社区；群体", "The local community helped build the garden.", "当地社区协助建成了这座花园。"),
        ("compare", "v.", "比较", "Compare the two maps before choosing a route.", "选择路线前，比较一下这两张地图。"),
        ("concern", "n.", "担忧；关切", "Air quality is a concern for local residents.", "空气质量是当地居民关心的问题。"),
        ("develop", "v.", "发展；培养", "Reading helps children develop language skills.", "阅读帮助儿童发展语言能力。"),
        ("efficient", "adj.", "高效的", "The new heating system is more efficient.", "新的供暖系统效率更高。"),
        ("environment", "n.", "环境", "A quiet environment helps me concentrate.", "安静的环境帮助我集中注意力。"),
        ("evidence", "n.", "证据；依据", "We need more evidence before drawing a conclusion.", "得出结论前，我们需要更多证据。"),
        ("feature", "n.", "特点；特色", "Large windows are a feature of this library.", "大窗户是这座图书馆的一个特色。"),
        ("focus", "v.", "集中注意力", "I find it easier to focus in the morning.", "我发现早上更容易集中注意力。"),
        ("improve", "v.", "改善；提高", "Regular practice can improve pronunciation.", "经常练习可以改善发音。"),
        ("include", "v.", "包括；包含", "The price will include breakfast.", "这个价格将包含早餐。"),
        ("increase", "v.", "增加；增长", "Longer opening hours may increase visitor numbers.", "延长开放时间可能会增加访客人数。"),
        ("issue", "n.", "问题；议题", "Water use is an important issue in this region.", "用水是这个地区的一个重要议题。"),
        ("maintain", "v.", "维持；维护", "Regular checks help maintain the equipment.", "定期检查有助于维护设备。"),
        ("opportunity", "n.", "机会", "The course offers an opportunity to learn new skills.", "这门课程提供了学习新技能的机会。"),
        ("participate", "v.", "参加；参与", "Students can participate in the discussion online.", "学生可以在线参与讨论。"),
        ("reduce", "v.", "减少；降低", "Taking the bus can reduce travel costs.", "乘公交车可以降低出行费用。"),
        ("require", "v.", "需要；要求", "Some plants require very little water.", "有些植物只需要很少的水。"),
        ("resource", "n.", "资源", "Fresh water is a valuable resource.", "淡水是一种宝贵的资源。"),
        ("respond", "v.", "回应；作出反应", "How do birds respond to changes in temperature?", "鸟类如何应对温度的变化？"),
        ("significant", "adj.", "显著的；重要的", "The study found a significant difference between the groups.", "这项研究发现组间存在显著差异。"),
        ("support", "v.", "支持", "The survey results support this idea.", "调查结果支持这一想法。"),
        ("trend", "n.", "趋势", "The chart shows a trend towards smaller households.", "图表显示出家庭规模缩小的趋势。"),
    ],
    "advanced": [
        ("allocate", "v.", "分配", "The council will allocate more money to public transport.", "市政委员会将为公共交通分配更多资金。"),
        ("alternative", "n.", "替代选择", "Cycling is an alternative to driving for short trips.", "短途出行时，骑车是开车之外的一种选择。"),
        ("anticipate", "v.", "预期；预料", "We anticipate a rise in demand during the summer.", "我们预计夏季需求会增加。"),
        ("assess", "v.", "评估", "The team will assess the impact of the new policy.", "团队将评估新政策的影响。"),
        ("attribute", "v.", "把……归因于", "They attribute the improvement to better training.", "他们将这一改善归因于更好的培训。"),
        ("capacity", "n.", "容量；承载能力", "The hall has a capacity of two hundred people.", "这个大厅可容纳两百人。"),
        ("considerable", "adj.", "相当大的", "Restoring the old building took considerable effort.", "修复这座老建筑花费了相当大的力气。"),
        ("consume", "v.", "消耗", "Older machines often consume more energy.", "较旧的机器往往消耗更多能源。"),
        ("contribute", "v.", "贡献；促成", "Small changes can contribute to a cleaner city.", "小的改变也能为更清洁的城市作出贡献。"),
        ("decline", "n.", "下降；减少", "The town recorded a decline in traffic noise.", "该镇记录到交通噪声有所下降。"),
        ("diverse", "adj.", "多样的", "The city has a diverse range of cultural activities.", "这座城市有多种多样的文化活动。"),
        ("emerge", "v.", "出现；显现", "New questions began to emerge during the discussion.", "讨论过程中开始出现新的问题。"),
        ("enhance", "v.", "提高；增强", "Good lighting can enhance the reading experience.", "良好的照明可以改善阅读体验。"),
        ("establish", "v.", "建立；确立", "The project aims to establish a local seed bank.", "该项目旨在建立一个本地种子库。"),
        ("exceed", "v.", "超过", "The cost may exceed our original budget.", "费用可能会超过我们最初的预算。"),
        ("facilitate", "v.", "促进；使便利", "Clear signs facilitate movement around the station.", "清晰的标识让人们在车站内通行更方便。"),
        ("feasible", "adj.", "可行的", "We need to check whether the plan is feasible.", "我们需要核实这个计划是否可行。"),
        ("fluctuate", "v.", "波动", "Energy prices fluctuate throughout the year.", "能源价格全年都在波动。"),
        ("implement", "v.", "实施", "The school will implement the changes next term.", "学校将在下学期实施这些改变。"),
        ("incentive", "n.", "激励；鼓励措施", "Lower fares provide an incentive to use public transport.", "较低的票价鼓励人们使用公共交通。"),
        ("inevitable", "adj.", "不可避免的", "Some delay was inevitable during the repairs.", "维修期间出现一些延误是不可避免的。"),
        ("insight", "n.", "深入了解；洞见", "The interviews offer insight into students' daily lives.", "访谈有助于深入了解学生的日常生活。"),
        ("obtain", "v.", "获得", "Visitors can obtain a map at the entrance.", "游客可以在入口处获取地图。"),
        ("perceive", "v.", "察觉；看待", "People may perceive the same risk differently.", "人们可能以不同方式看待同一种风险。"),
        ("potential", "adj.", "潜在的", "The team discussed the potential benefits of the project.", "团队讨论了该项目的潜在益处。"),
        ("reliable", "adj.", "可靠的", "Researchers need reliable measurements.", "研究人员需要可靠的测量结果。"),
        ("restore", "v.", "恢复；修复", "Volunteers helped restore the old garden.", "志愿者帮助修复了这座老花园。"),
        ("sustainable", "adj.", "可持续的", "The city is exploring more sustainable ways to travel.", "这座城市正在探索更可持续的出行方式。"),
        ("undergo", "v.", "经历；经受", "The bridge will undergo repairs this winter.", "这座桥将在今年冬季进行维修。"),
        ("widespread", "adj.", "广泛的；普遍的", "The proposal received widespread support.", "这项提议获得了广泛支持。"),
    ],
    "academic": [
        ("abstract", "n.", "摘要", "The abstract outlines the main findings of the study.", "摘要概述了这项研究的主要发现。"),
        ("aggregate", "v.", "汇总", "We aggregate the responses before comparing the groups.", "我们先汇总回答，再比较各组。"),
        ("assumption", "n.", "假设；前提", "The model relies on an assumption about population growth.", "该模型依赖于一个关于人口增长的假设。"),
        ("bias", "n.", "偏差；偏倚", "The sampling method may introduce bias.", "这种抽样方法可能引入偏差。"),
        ("coherent", "adj.", "连贯的；条理清楚的", "The author presents a coherent explanation of the results.", "作者对结果作出了条理清楚的解释。"),
        ("constraint", "n.", "限制；约束", "Limited time was a constraint on the study.", "时间有限是这项研究面临的一项约束。"),
        ("correlation", "n.", "相关性", "A correlation does not by itself establish causation.", "相关性本身并不能确立因果关系。"),
        ("criterion", "n.", "标准（单数）", "Cost was one criterion used to compare the options.", "成本是比较各方案时采用的一项标准。"),
        ("derive", "v.", "推导；获得", "We derive the estimate from the available data.", "我们根据现有数据推导出这一估计值。"),
        ("empirical", "adj.", "基于实证的", "The claim needs empirical support.", "这一主张需要实证支持。"),
        ("framework", "n.", "框架", "The paper proposes a framework for comparing policies.", "这篇论文提出了一个比较政策的框架。"),
        ("hypothesis", "n.", "假说；假设", "The experiment was designed to test this hypothesis.", "该实验旨在检验这一假说。"),
        ("implication", "n.", "可能的影响；含义", "The findings have implications for future course design.", "这些发现对今后的课程设计具有启示。"),
        ("infer", "v.", "推断", "We cannot infer a general rule from one example.", "我们无法从单个例子推断出一般规律。"),
        ("interpret", "v.", "解释；理解", "The researchers interpret the pattern with caution.", "研究人员谨慎地解释这一模式。"),
        ("longitudinal", "adj.", "纵向的；长期追踪的", "A longitudinal study followed the same students for five years.", "一项纵向研究对同一批学生追踪了五年。"),
        ("methodology", "n.", "方法体系；研究方法", "The methodology section explains how the data were collected.", "研究方法部分解释了数据是如何收集的。"),
        ("parameter", "n.", "参数", "Changing this parameter affects the model's predictions.", "改变这个参数会影响模型的预测。"),
        ("preliminary", "adj.", "初步的", "These preliminary results need further testing.", "这些初步结果需要进一步检验。"),
        ("qualitative", "adj.", "定性的", "The team used qualitative interviews to explore these experiences.", "团队通过定性访谈来探究这些经历。"),
        ("quantitative", "adj.", "定量的", "The report includes a quantitative analysis of travel times.", "报告包括对出行时间的定量分析。"),
        ("replicate", "v.", "重复；复现", "Another team tried to replicate the experiment.", "另一个团队尝试复现该实验。"),
        ("robust", "adj.", "稳健的", "The result remained robust across several different settings.", "在几种不同设置下，这一结果仍然稳健。"),
        ("scope", "n.", "范围", "The scope of this study is limited to urban schools.", "这项研究的范围仅限于城市学校。"),
        ("subsequent", "adj.", "随后的", "Subsequent experiments used a larger sample.", "随后的实验采用了更大的样本。"),
        ("synthesize", "v.", "综合", "The review aims to synthesize findings from several studies.", "这篇综述旨在综合多项研究的发现。"),
        ("underlying", "adj.", "深层的；潜在的", "The study explores the underlying causes of the change.", "这项研究探讨了这一变化的深层原因。"),
        ("validate", "v.", "验证", "We used a separate dataset to validate the model.", "我们使用另一份数据集来验证模型。"),
        ("variable", "n.", "变量", "Temperature was the only variable changed in the experiment.", "温度是实验中唯一被改变的变量。"),
        ("whereas", "conj.", "然而；而", "The first group improved, whereas the second showed little change.", "第一组有所改善，而第二组几乎没有变化。"),
    ],
}

WORDS = tuple(Word(term, level, part, meaning, example, translation)
              for level, rows in _ROWS.items()
              for term, part, meaning, example, translation in rows)
WORD_BY_ID = {word.term: word for word in WORDS}
