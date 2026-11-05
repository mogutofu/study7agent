# -*- coding: utf-8 -*-
"""生物模块：高中生物（人教版 2019）核心过程的**分步动画 + 文字说明**。

为什么做成"数据 + 通用播放器"：清单里有 50 多个过程，每个手画一套动画既贵又不一致。
这里把每个过程写成 **名称 + 一组阶段文字**，图由 `kind` 决定用哪种通用画法：

  chain     链式过程（一步接一步：脱水缩合、分泌蛋白、呼吸三阶段、免疫…）
  cycle     循环过程（卡尔文循环、ATP-ADP、碳循环、血糖调节…）
  membrane  膜两侧 / 电位（渗透、质壁分离、主动运输、静息与动作电位、流动镶嵌）
  curve     曲线（酶活性、J/S 形增长、生长素两重性、电位变化曲线…）
  grid      棋盘格（一对/两对相对性状杂交的性状分离比）
  tree      分支（细胞分化、遗传系谱）

播放器（webui/ui/bio.js）负责：章节/过程选择、播放暂停、上一步下一步、时间轴、阶段文字。
"""

import json
import os

#: (key, 章节, 名称, 画法, 曲线形状, [阶段文字…])
PROCESSES = [
    # ---------------- 必修1 分子与细胞 ----------------
    ("dehydration", "必修1", "脱水缩合形成肽键", "chain", None, [
        "两个氨基酸靠近：一个的羧基（—COOH）对着另一个的氨基（—NH2）",
        "羧基脱去 —OH、氨基脱去 —H，形成一个肽键 —CO—NH—",
        "同时脱下一分子水：n 个氨基酸连成一条肽链，脱水 n-1 个、肽键 n-1 个"]),
    ("protein_structure", "必修1", "蛋白质空间结构（一级到四级）", "chain", None, [
        "一级：氨基酸按顺序连成多肽链（肽键连接）",
        "二级：肽链局部盘曲/折叠成 α 螺旋、β 折叠（靠氢键）",
        "三级：整条肽链再折叠成特定的空间结构（靠二硫键、疏水作用等）",
        "四级：多条肽链聚在一起（如血红蛋白的 4 条链）——结构决定功能"]),
    ("membrane_model", "必修1", "细胞膜流动镶嵌模型", "membrane", None, [
        "磷脂双分子层做基本骨架：亲水头朝外、疏水尾朝内",
        "蛋白质分子镶嵌、贯穿或覆盖在磷脂双分子层上",
        "磷脂和大多数蛋白质都能侧向流动——所以膜有流动性"]),
    ("secretory_path", "必修1", "分泌蛋白的合成与运输", "chain", None, [
        "核糖体：把氨基酸连成肽链（附着在内质网上）",
        "内质网：初步加工、折叠，出芽形成囊泡",
        "高尔基体：再加工、分类、包装，再出芽形成囊泡",
        "囊泡与细胞膜融合，把蛋白质分泌到细胞外",
        "膜面积：内质网减小、高尔基体先增后减、细胞膜增大"]),
    ("osmosis", "必修1", "渗透作用原理", "membrane", None, [
        "半透膜两侧溶液浓度不同（外侧低、内侧高）",
        "水分子可以自由通过半透膜，溶质分子不能",
        "单位时间内进入高浓度一侧的水分子更多——水往高浓度一侧净移动",
        "结果：高浓度一侧液面上升，直到两侧浓度差被抵消"]),
    ("plasmolysis", "必修1", "质壁分离与复原", "membrane", None, [
        "外界溶液浓度 > 细胞液浓度：细胞失水",
        "原生质层（细胞膜+液泡膜+两层膜之间的细胞质）收缩，与细胞壁分离",
        "换成清水：细胞吸水，质壁分离复原（活的成熟植物细胞才做得到）"]),
    ("active_transport", "必修1", "主动运输（逆浓度梯度）", "membrane", None, [
        "载体蛋白与被运输的分子结合（有特异性）",
        "消耗 ATP 供能，载体蛋白构象改变",
        "把物质从低浓度一侧运到高浓度一侧——所以能逆浓度梯度运输"]),
    ("enzyme_barrier", "必修1", "酶降低活化能", "curve", "barrier", [
        "没有酶时：反应物要先越过很高的能垒（活化能）才变成产物",
        "加酶后：反应走一条更低的路（酶与底物结合成中间产物）",
        "活化能降低，所以常温下反应也能很快进行；酶本身反应前后不变"]),
    ("enzyme_conditions", "必修1", "温度与 pH 对酶活性的影响", "curve", "peak", [
        "在最适温度/pH：酶活性最高（曲线顶点）",
        "偏离最适值：活性下降；过酸过碱、高温会让酶变性失活",
        "低温只是抑制（酶不变性），回温后活性可以恢复"]),
    ("atp_adp", "必修1", "ATP 与 ADP 的相互转化", "cycle", None, [
        "ATP 远离腺苷的那个高能磷酸键断裂，释放能量，变成 ADP + Pi",
        "放出的能量用于各项生命活动（主动运输、肌肉收缩、合成反应）",
        "呼吸作用/光合作用释放的能量把 ADP 和 Pi 重新合成 ATP——循环不断"]),
    ("aerobic_respiration", "必修1", "有氧呼吸三阶段", "chain", None, [
        "第一阶段（细胞质基质）：葡萄糖分解成丙酮酸 + [H]，放少量能量",
        "第二阶段（线粒体基质）：丙酮酸和水彻底分解成 CO2 + [H]，放少量能量",
        "第三阶段（线粒体内膜）：[H] 与 O2 结合成水，放出大量能量（生成最多 ATP）"]),
    ("light_reaction", "必修1", "光反应（类囊体薄膜）", "membrane", None, [
        "叶绿体中的色素吸收光能",
        "水在光下分解：产生 O2、[H]（NADPH）和电子",
        "同时把光能转成 ATP 里的化学能（ADP + Pi -> ATP）",
        "产物 [H] 和 ATP 送到叶绿体基质，供暗反应使用"]),
    ("calvin_cycle", "必修1", "暗反应（卡尔文循环）", "cycle", None, [
        "CO2 与 C5 结合（CO2 的固定），生成 2 个 C3",
        "C3 被光反应送来的 [H] 还原、并由 ATP 供能，生成有机物（糖类）",
        "同时再生出 C5，继续去固定 CO2——循环进行"]),
    ("mitosis", "必修1", "有丝分裂全过程", "chain", None, [
        "间期：DNA 复制、有关蛋白质合成（染色体数不变、DNA 加倍）",
        "前期：核膜核仁消失、染色质变成染色体、纺锤体形成",
        "中期：染色体的着丝点排在赤道板上（观察染色体形态数目的最佳时期）",
        "后期：着丝点分裂，姐妹染色单体分开，分别移向两极（染色体数暂时加倍）",
        "末期：核膜核仁重建、纺锤体消失、细胞质分裂成两个子细胞"]),
    ("mitosis_curve", "必修1", "有丝分裂中 DNA 与染色体数目变化", "curve", "mitosis", [
        "间期：DNA 复制，DNA 数由 2N 增到 4N；染色体数仍是 2N",
        "前期、中期：DNA 4N、染色体 2N（每条染色体含 2 条单体）",
        "后期：着丝点分裂，染色体数由 2N 增到 4N，DNA 仍 4N",
        "末期：细胞一分为二，DNA 和染色体数都回到 2N"]),
    ("cell_differentiation", "必修1", "细胞分化（基因选择性表达）", "tree", None, [
        "受精卵通过有丝分裂产生一群相同的细胞（遗传物质相同）",
        "不同细胞里不同基因被选择性表达（转录出不同的 mRNA）",
        "结果：形态、结构、功能不同的细胞群，构成各种组织、器官"]),
    # ---------------- 必修2 遗传与进化 ----------------
    ("monohybrid", "必修2", "一对相对性状的杂交实验", "grid", "mono", [
        "P：纯合高茎（DD） x 纯合矮茎（dd）",
        "F1：全是高茎（Dd）——说明高茎是显性",
        "F1 自交：F2 出现性状分离，高茎 : 矮茎 = 3 : 1"]),
    ("segregation_law", "必修2", "分离定律的实质", "chain", None, [
        "F1（Dd）产生配子时，等位基因 D 与 d 随同源染色体分开",
        "分别进入不同的配子：配子中只含 D 或 d（比例 1:1）",
        "受精时配子随机结合，F2 出现 DD : Dd : dd = 1 : 2 : 1，性状比 3 : 1"]),
    ("dihybrid", "必修2", "两对相对性状的杂交实验", "grid", "di", [
        "P：黄色圆粒（YYRR） x 绿色皱粒（yyrr）",
        "F1：全是黄色圆粒（YyRr）",
        "F1 自交：F2 出现 4 种表现型，黄圆 : 黄皱 : 绿圆 : 绿皱 = 9 : 3 : 3 : 1"]),
    ("independent_assortment", "必修2", "自由组合定律的实质", "chain", None, [
        "F1 产生配子时，同源染色体上的等位基因分离",
        "非同源染色体上的非等位基因自由组合",
        "所以 YyRr 产生 4 种配子 YR、Yr、yR、yr，比例 1:1:1:1"]),
    ("meiosis", "必修2", "减数分裂全过程", "chain", None, [
        "间期：DNA 复制，染色体数不变",
        "减 I 前期：同源染色体联会形成四分体，非姐妹染色单体可能交叉互换",
        "减 I 后期：同源染色体分离，分别移向两极（非同源染色体自由组合）",
        "减 II：着丝点分裂，姐妹染色单体分开；结果产生 4 个子细胞，染色体数减半"]),
    ("meiosis_curve", "必修2", "减数分裂中 DNA 与染色体数目变化", "curve", "meiosis", [
        "间期：DNA 复制，2N -> 4N",
        "减 I 末期：细胞一分为二，DNA 由 4N 减到 2N，染色体由 2N 减到 N",
        "减 II 后期：着丝点分裂，染色体数暂时由 N 增到 2N",
        "减 II 末期：形成 4 个细胞，DNA 和染色体数都变成 N"]),
    ("phage_experiment", "必修2", "噬菌体侵染细菌实验", "chain", None, [
        "用 35S 标记蛋白质外壳、用 32P 标记 DNA，分别侵染细菌",
        "保温、搅拌、离心：上清液和沉淀分开",
        "35S 组放射性主要在上清液；32P 组主要在沉淀（细菌）",
        "结论：进入细菌的是 DNA，DNA 才是遗传物质"]),
    ("dna_structure", "必修2", "DNA 双螺旋结构", "chain", None, [
        "两条脱氧核苷酸链反向平行，盘成右手双螺旋",
        "外侧是磷酸和脱氧核糖交替连接构成的基本骨架",
        "内侧碱基对靠氢键连接，A—T 之间 2 个氢键、G—C 之间 3 个氢键",
        "碱基互补配对保证复制时能准确配对"]),
    ("dna_replication", "必修2", "DNA 半保留复制", "chain", None, [
        "解旋：氢键断开，双链解开成两条模板",
        "以每条母链为模板、按碱基互补配对原则合成子链",
        "每条子代 DNA 都保留一条母链、一条新链——半保留复制",
        "n 次复制后得到 2^n 个 DNA，其中含母链的始终是 2 个"]),
    ("transcription", "必修2", "转录（DNA -> mRNA）", "chain", None, [
        "DNA 局部解旋，露出模板链",
        "RNA 聚合酶以核糖核苷酸为原料，按碱基互补配对合成 mRNA（A-U、T-A、G-C）",
        "mRNA 从 DNA 上脱落，DNA 恢复双螺旋，mRNA 去细胞质指导翻译"]),
    ("translation", "必修2", "翻译（mRNA -> 蛋白质）", "chain", None, [
        "mRNA 与核糖体结合，核糖体沿 mRNA 移动",
        "tRNA 带着氨基酸来，其反密码子与 mRNA 上的密码子互补配对",
        "氨基酸被连到肽链上，核糖体向前移动一个密码子",
        "肽链延伸到终止密码子，脱离核糖体并折叠成蛋白质"]),
    ("gene_mutation", "必修2", "基因突变的类型", "chain", None, [
        "碱基对替换：可能只改变一个氨基酸，也可能不变（密码子简并）",
        "碱基对增添：插入一个碱基，读码框后移，后面氨基酸大多改变",
        "碱基对缺失：缺失一个碱基，同样造成读码框移动",
        "结果：蛋白质结构改变——性状改变；基因突变是变异的根本来源"]),
    ("chromosome_variation", "必修2", "染色体结构变异", "chain", None, [
        "缺失：染色体上少了一段（如猫叫综合征）",
        "重复：某一段多了一份",
        "倒位：一段的排列顺序反了（位置颠倒）",
        "易位：一段接到了非同源染色体上——四种都属于结构变异"]),
    ("pedigree", "必修2", "遗传系谱图分析", "tree", None, [
        "先看是否为伴 Y：只有男性患病 -> 伴 Y",
        "无中生有（双亲正常、子代患病）-> 隐性；有中生无 -> 显性",
        "隐性看女病：女患者的父亲或儿子必病 -> 伴 X 隐性，否则常染色体",
        "再按基因型计算后代患病概率"]),
    ("natural_selection_freq", "必修2", "自然选择对基因频率的影响", "curve", "selection", [
        "定向选择：环境一直朝一个方向选，比如工业黑化，基因频率单向变化",
        "稳定选择：中间型个体更适应，两端被淘汰，基因频率趋于稳定",
        "分裂选择：两端更适应，中间型被淘汰，种群可能分成两支",
        "不论哪种，改变的都是**基因频率**——这就是进化的实质"]),
    # ---------------- 选择性必修1 稳态与调节 ----------------
    ("internal_environment", "选必1", "内环境的组成与关系", "chain", None, [
        "血浆：血细胞生活的环境，在血管里流动",
        "组织液：组织细胞生活的环境，由血浆通过毛细血管壁滤过形成",
        "淋巴：由一部分组织液进入毛细淋巴管形成，最终汇入血浆",
        "三者关系：血浆 -> 组织液 -> 淋巴 -> 血浆，物质交换靠它们完成"]),
    ("resting_potential", "选必1", "静息电位（外正内负）", "membrane", None, [
        "静息时膜主要对 K+ 有通透性（K+ 通道开放）",
        "膜内 K+ 浓度高，K+ 顺浓度梯度外流",
        "膜外正电荷增多、膜内变负——形成外正内负的静息电位（约 -70 mV）"]),
    ("action_potential", "选必1", "动作电位（外负内正）", "curve", "potential", [
        "刺激达到阈电位：Na+ 通道大量开放，Na+ 内流",
        "膜内变正、膜外变负——形成动作电位的上升支（去极化）",
        "随后 Na+ 通道关闭、K+ 通道开放，K+ 外流，膜电位下降（复极化）",
        "恢复静息电位，期间有短暂的不应期"]),
    ("nerve_conduction", "选备1-传导", "兴奋在神经纤维上的传导", "chain", None, [
        "受刺激部位膜电位反转，与相邻未兴奋部位形成电位差",
        "局部电流：膜外从未兴奋部位流向兴奋部位，膜内相反",
        "局部电流刺激相邻部位，使其也产生动作电位——兴奋向前传导",
        "双向传导；传导过程中动作电位的幅度不衰减"]),
    ("potential_curve", "选必1", "电位变化曲线（可调刺激强度）", "curve", "potential", [
        "阈下刺激：只产生局部电位，不形成动作电位",
        "阈刺激/阈上刺激：产生一个固定幅度的动作电位（全或无）",
        "曲线看：去极化（上升）、复极化（下降）、超极化（低于静息电位）",
        "增大刺激强度只会增加动作电位的**频率**，不改变幅度"]),
    ("synapse", "选必1", "突触传递过程", "chain", None, [
        "兴奋传到轴突末梢，突触小泡与突触前膜融合",
        "神经递质释放到突触间隙，扩散到突触后膜",
        "递质与后膜上的特异性受体结合，引起后膜电位变化（兴奋或抑制）",
        "递质被酶分解或回收——所以突触传递是**单向**的"]),
    ("blood_sugar", "选必1", "血糖平衡调节", "cycle", None, [
        "进食后血糖升高：胰岛 B 细胞分泌胰岛素",
        "胰岛素促进组织细胞摄取、利用、储存葡萄糖，血糖下降",
        "血糖降低时：胰岛 A 细胞分泌胰高血糖素，促进肝糖原分解、非糖物质转化",
        "两者拮抗，加上神经调节，把血糖维持在 0.8~1.2 g/L"]),
    ("thyroid_axis", "选必1", "甲状腺激素的分级调节", "chain", None, [
        "下丘脑分泌促甲状腺激素释放激素（TRH）",
        "TRH 作用于垂体，使其分泌促甲状腺激素（TSH）",
        "TSH 作用于甲状腺，促进甲状腺激素合成与分泌",
        "甲状腺激素过多时反过来抑制下丘脑和垂体——负反馈调节"]),
    ("humoral_immunity", "选必1", "体液免疫全过程", "chain", None, [
        "抗原被吞噬细胞处理、呈递给 T 细胞，再呈递给 B 细胞",
        "B 细胞受刺激后，在淋巴因子作用下增殖分化",
        "形成浆细胞（效应 B 细胞）和记忆细胞",
        "浆细胞分泌抗体，抗体与抗原特异性结合，形成沉淀或细胞集团被清除"]),
    ("cellular_immunity", "选必1", "细胞免疫全过程", "chain", None, [
        "抗原呈递细胞把抗原呈递给 T 细胞",
        "T 细胞增殖分化成细胞毒性 T 细胞和记忆 T 细胞",
        "细胞毒性 T 细胞识别并接触靶细胞（被感染的细胞、癌细胞）",
        "使靶细胞裂解死亡，释放出的抗原再由抗体清除"]),
    ("auxin", "选必1", "生长素作用的两重性", "curve", "auxin", [
        "低浓度：促进生长（曲线上升段）",
        "最适浓度：促进作用最强（曲线顶点，如 10^-8 mol/L 左右）",
        "浓度继续升高：促进作用减弱；再高就抑制生长（曲线降到横轴以下）",
        "所以同一种生长素，浓度不同效果可能完全相反"]),
    # ---------------- 选择性必修2 生物与环境 ----------------
    ("population_growth", "选必2", "J 形与 S 形增长", "curve", "growth", [
        "J 形：食物空间充裕、无敌害时，种群数量按 Nt = N0 x λ^t 指数增长",
        "S 形：资源有限，增长先快后慢，最后稳定在 K 值（环境容纳量）",
        "K/2 时增长速率最大——渔业捕捞后要剩下 K/2",
        "K 值时出生率约等于死亡率，种群数量相对稳定"]),
    ("species_relation", "选必2", "种间关系曲线", "curve", "relation", [
        "竞争：两种生物争夺同样资源，常出现此消彼长（或一方被淘汰）",
        "捕食：捕食者数量变化滞后于被捕食者（曲线一前一后）",
        "互利共生：两种生物数量同增同减，曲线基本平行",
        "寄生：寄主数量多、寄生虫少，寄生者依附寄主"]),
    ("energy_flow", "选必2", "能量流动过程", "chain", None, [
        "输入：生产者固定太阳能（总能量）",
        "传递：沿食物链逐级传递，每个营养级只传到下一级一部分",
        "转化：一部分用于自身呼吸消耗、一部分用于生长发育繁殖",
        "散失：呼吸作用以热能形式散失；传递效率约 10%~20%"]),
    ("carbon_cycle", "选必2", "碳循环", "cycle", None, [
        "无机环境 -> 生物群落：生产者光合作用固定 CO2",
        "群落内部：沿食物链传递（含碳有机物）",
        "生物群落 -> 无机环境：生产者、消费者的呼吸作用释放 CO2",
        "分解者分解遗体残骸；化石燃料燃烧也释放大量 CO2"]),
    # ---------------- 选择性必修3 生物技术与工程 ----------------
    ("tissue_culture", "选必3", "植物组织培养", "chain", None, [
        "取外植体（离体的植物组织）消毒",
        "脱分化：在激素作用下形成愈伤组织（无定形的薄壁细胞团）",
        "再分化：先出芽后生根（生长素/细胞分裂素比例决定芽或根）",
        "形成完整植株——体现植物细胞的全能性"]),
    ("protoplast_fusion", "选必3", "植物体细胞杂交", "chain", None, [
        "用纤维素酶和果胶酶去掉细胞壁，得到原生质体",
        "诱导原生质体融合（离心、振动、电激、聚乙二醇）",
        "融合后的杂种细胞经组织培养形成愈伤组织",
        "再分化成杂种植株——克服了远缘杂交不亲和的障碍"]),
    ("monoclonal_antibody", "选必3", "单克隆抗体制备", "chain", None, [
        "用特定抗原免疫小鼠，从小鼠脾脏取 B 淋巴细胞",
        "B 淋巴细胞与骨髓瘤细胞融合成杂交瘤细胞",
        "筛选：选出既能无限增殖又能产生特定抗体的细胞",
        "克隆培养、体内或体外培养，大量制备单克隆抗体"]),
    ("nuclear_transfer", "选必3", "核移植技术（克隆动物）", "chain", None, [
        "取供体细胞的细胞核",
        "取卵母细胞并去核（去掉细胞核）",
        "把供体核移入去核卵母细胞，形成重组细胞",
        "电刺激/化学方法激活，胚胎移植到代孕母体，发育成克隆动物"]),
    ("restriction_enzyme", "选必3", "限制酶切割 DNA", "chain", None, [
        "限制酶识别特定的核苷酸序列（如 GAATTC 回文序列）",
        "在特定切点上切开磷酸二酯键",
        "切口错开则产生黏性末端，平齐切开则产生平末端",
        "同一种限制酶切出的黏性末端可以互补配对，便于连接"]),
    ("pcr", "选必3", "PCR 过程", "cycle", None, [
        "变性（90 度以上）：双链 DNA 解链成单链",
        "退火（50 度左右）：引物与模板互补结合",
        "延伸（72 度左右）：耐高温 DNA 聚合酶沿引物延伸合成新链",
        "重复约 30 次循环，DNA 数量按 2^n 指数扩增"]),
    ("expression_vector", "选必3", "基因表达载体的构建", "chain", None, [
        "用同种限制酶分别切割目的基因和质粒（载体）",
        "用 DNA 连接酶把目的基因连到质粒上",
        "载体必须含：启动子、目的基因、终止子、标记基因",
        "启动子是 RNA 聚合酶识别结合的部位，标记基因用来筛选"]),
    ("gene_transfer", "选必3", "目的基因导入受体细胞", "chain", None, [
        "植物：农杆菌转化法（最常用）、基因枪法、花粉管通道法",
        "动物：显微注射法（受精卵），再胚胎移植",
        "微生物：用 Ca2+ 处理大肠杆菌成感受态细胞，再热激",
        "导入后要检测：是否插入、是否转录、是否翻译"]),
]

#: 每种曲线用哪几条数据（渲染时按公式算，不写死坐标）
CURVE_KINDS = ("barrier", "peak", "mitosis", "meiosis", "potential", "auxin", "growth",
               "relation", "selection")

CHAPTERS = ["必修1", "必修2", "选必1", "选备1-传导", "选必2", "选必3"]

#: 现成插图放在 webui/ui/vendor/bio/<key>.gif|png|jpg（scripts/fetch-bio-media.py 从 Wikimedia
#: Commons 下的动画 GIF / 课本插图，来源与授权写在同目录的 README.txt 里）。
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "vendor", "bio")


def media(key):
    """这个过程的现成插图（Commons 的动画 GIF 或静态插图），没有就返回 None。"""
    for suffix in (".svg", ".gif", ".png", ".jpg"):
        if os.path.isfile(os.path.join(MEDIA_DIR, key + suffix)):
            return "vendor/bio/" + key + suffix
    return None

#: 每张现成插图的中文说明（原图多是英/法/德/波兰文，老师要用中文讲清楚）
MEDIA_NOTES = {
    'action_potential': '这是动画 GIF，只有 3 帧，每帧一幅示意图，文字是英文，共 6 处：Outside of cell = 细胞外；Inside of cell = 细胞内；1 - Cell at rest = 1 - 细胞处于静息状态；2 - Stimulus triggers action potential = 2 - 刺激引发动作电位；Ions pass through membrane = 离子穿过膜；3 - Action potential spreads = 3 - 动作电位扩散。另外图中还有多处 "+"（正号）和 "-"（负号）表示膜两侧的电荷符号。第 1 帧：上下两排黄色圆圈表示膜两侧，上方标 Outside of cell 并配一排 "+"，下方标 Inside of cell 并配一排 "-"，右侧写 1 - Cell at rest。第 2 帧：一个红色双向箭头穿过细胞膜，"+" 与 "-" 在箭头附近上下交错，右侧写 2 - Stimulus triggers action potential 与 Ions pass through membrane。第 3 帧：膜内外出现蓝色和绿色的弧形箭头，"+" 与 "-" 的位置局部对调，右侧写 3 - Action potential spreads。',
    'aerobic_respiration': '原图文字本身就是中文，这是已经汉化过的版本，图上没有外文。逐条标注：膜间腔、细胞色素c、线粒体内膜、ATP合酶、基质、NADH、琥珀酸、UQ，以及罗马数字Ⅰ、Ⅱ、Ⅲ、Ⅳ，另外还有符号 H⁺、½O₂、H₂O。图意：这是有氧呼吸第三阶段（电子传递链）的示意图。线粒体内膜上画着四个跨膜复合体Ⅰ～Ⅳ，NADH 在复合体Ⅰ、琥珀酸在复合体Ⅱ把电子交出来，电子经 UQ（泛醌）和细胞色素c依次传给复合体Ⅲ和Ⅳ，最后在复合体Ⅳ处把 ½O₂ 还原成 H₂O。与此同时 H⁺ 被泵进膜间腔，膜间腔里的 H⁺ 再顺浓度差经 ATP合酶流回基质，驱动 ATP 的合成——图右侧把 ATP合酶画成一条让 H⁺ 通过的跨膜通道，就是表达这个意思。图中箭头表示电子与 H⁺ 的流向，没有别的文字标注。',
    'atp_adp': '本说明依据 webui/ui/vendor/bio/atp_adp.gif（589×821，共 6 帧）写成：开头所述画面是动画的第 1 帧，全片只有 6 帧，已逐帧看过。逐帧核对后确认，整段动画没有任何文字、字母、数字、箭头或标注框，所以没有「原文 = 中文」可以列；文件名里的 ATP、ADP 字样并没有出现在画面中。画面主体是一个用彩色曲面画出的立体分子模型：上部是一大团玫瑰红与橙红色的球状团块，中间收窄成紫色的柱状部分，下部是蓝色、青色与品红色的团块，右侧另有一条淡橙色的细长弯臂从下方一直绕回到上方的头部旁边；团块表面能看到三个亮绿色的小片，头部中央偏右有一个亮黄色的小片。6 帧之间这个模型缓慢地绕竖直方向转动，各部分的大小和相对位置基本不变，只有朝向、遮挡关系和表面凹凸的投影在变，看上去像一个在自转的分子马达。因为图中没有任何说明文字，这里只能描述它的外形和转动，无法从画面判断各部分的名称。',
    'blood_sugar': '原图德文。逐条翻译：Glukose im Blut = 血液中的葡萄糖；mmol/L = 毫摩尔每升（左纵轴单位）；Serum Insulin = 血清胰岛素；pmol/L = 皮摩尔每升（右纵轴单位）；mg/dL = 毫克每分升（最左侧另一套刻度）；Glukose Blutlevel = 血糖水平（深红实线图例）；Insulin Blutlevel = 胰岛素水平（蓝色实线图例）；Stärkehaltiges Essen ([Glukose]n) = 含淀粉的食物（[葡萄糖]n）；Saccharosehaltiges Essen (Glukose-Fruktose) = 含蔗糖的食物（葡萄糖-果糖）；Frühstück = 早餐；Mittagessen = 午餐；Abendessen = 晚餐；basiert auf [Daly98] = 依据文献 [Daly98]。刻度数字：纵轴 3.0～8.0 mmol/L、0～400 pmol/L 与 72、90、108、126 mg/dL，横轴 7:00 至次日 7:00，每 2 小时一格。图意：一天之内血糖与血清胰岛素随时间起伏，横轴下方三个箭头分别标出早、中、晚三餐；每次进餐后血糖先升高，胰岛素随之上升把血糖压回去。后两条图例各由红、蓝两条线叠在一起（第三条是实线、第四条是点线），分别表示吃淀粉类食物和吃蔗糖类食物两种情形；图例上方还有一个黄色太阳和一个白色弯月图标，表示白天与夜间。',
    'calvin_cycle': '原图含葡萄牙文、英文、巴斯克文，每组均有英文兜底，中文浏览器实际显示英文：Ribulose 1,5-bisphosphate = 1,5-二磷酸核酮糖；Ribulose 5-phosphate = 5-磷酸核酮糖；Carbon Dioxide = 二氧化碳；3-phosphoglycerate = 3-磷酸甘油酸；1,3-bisphosphoglycerate = 1,3-二磷酸甘油酸；Glyceraldehyde 3-phosphate(G3P) = 3-磷酸甘油醛；Inorganic phosphate = 无机磷酸；ATP = 三磷酸腺苷；ADP = 二磷酸腺苷；NADPH = 还原型辅酶Ⅱ；NADP+ = 氧化型辅酶Ⅱ；RuBisCo = 核酮糖二磷酸羧化酶；Phase 1: Carbon Fixation = 第一阶段：碳固定；Phase 2: Reduction = 第二阶段：还原；Phase 3: Regeneration of Ribulose = 第三阶段：核酮糖再生；Central Metabolic Pathways = 中心代谢途径。图意：这是卡尔文循环。二氧化碳在 RuBisCo 处与 1,5-二磷酸核酮糖结合，生成 3-磷酸甘油酸（碳固定）；随后消耗 ATP 和 NADPH，把它还原成 3-磷酸甘油醛（还原）；最后一部分产物再消耗 ATP，把 5-磷酸核酮糖重新变成 1,5-二磷酸核酮糖，循环闭合，NADPH 变回 NADP+。图中央用三块扇形分开三个阶段，外圈粗箭头表示循环方向。',
    'carbon_cycle': '原图法文。逐条翻译：CYCLE DU CARBONE = 碳循环；ATMOSPHÈRE = 大气圈；BIOSPHÈRE = 生物圈；HYDROSPHÈRE = 水圈；LITHOSPHÈRE = 岩石圈；Photosynthèse = 光合作用；Respiration = 呼吸作用；dissolution = 溶解；Déforestation = 毁林；Volcanisme = 火山活动；Sédimentation = 沉积作用；Fossilisation = 化石化作用；Combustion massive de charbon, pétrole et gaz = 煤、石油和天然气的巨量燃烧；roches carbonatées (calcaire essentiel.) = 碳酸盐岩（主要是石灰岩）；roches carbonnées = 含碳岩石；magma = 岩浆；biosphère océanique = 海洋生物圈；végétale = 植物的；animale = 动物的；intervention humaine = 人类活动；Gt = 十亿吨。框内数字（单位 Gt）：大气 750、生物圈 2 000、水圈 39 000、碳酸盐岩 30 000 000、含碳岩石 7 000 000；箭头上的数字（单位 Gt/年）：光合作用 62、呼吸作用 60、溶解 92、毁林 1.9、人类燃烧 5 至 6、沉积 0.3、火山 0.1、化石化 0.1。大气框里另标 CO₂、CH₄，水圈里另标 CO₂、CO₃²⁻、Ca²⁺。图意：大气 CO₂ 被光合作用固定进生物圈，生物圈靠呼吸作用把碳还给大气；大气 CO₂ 溶解入海，海中碳酸盐沉积成岩，岩石经火山活动把碳送回大气。四个彩色框就是四个碳库：框里的大数字是贮存量，岩石圈最大、大气最小；箭头上的数字是每年的流通量。黑色点线箭头是唯一的人类活动通路：毁林加燃烧化石燃料，把地下封存很久的碳快速放回大气。整张图就是一本碳循环的收支账，四个彩框是碳库，箭头是流通。',
    'cell_differentiation': '这是动画 GIF，共 31 帧（抽样核对了 12 帧）。图中文字只有左上角的显微图像叠加信息，是英文与数字：10 µm = 10 微米（比例尺，图上写作 10 µm）；00:32:40 hours = 时间戳（时:分:秒），首帧就是这个值。逐帧时间戳递增，抽样看到 00:34:40、00:36:00、00:38:40、00:40:40、00:43:40、00:45:00、00:47:40、00:49:40、00:52:00、00:53:40 hours，抽样到的末帧是 00:56:40 hours。画面是黑底灰度显微延时影像：中央一团细胞边缘伸出许多细长的丝状突起，内部有颗粒感，右侧有一块较亮的团块。随时间推移，这团细胞不断变形，向右、向下伸展，右下方逐渐拉出一条内部带颗粒的带状结构。除比例尺和时间戳外，画面没有其它文字或标注。',
    'cellular_immunity': '原图英文。逐条翻译：1) T-cells are mobilized when they encounter a cell such as a dendritic cell or B-cell that has digested an antigen and is displaying antigen fragments bound to its MHC molecules. = T 细胞遇到“已消化抗原、并把抗原片段结合在自身 MHC 分子上展示出来”的细胞（如树突状细胞或 B 细胞）时被动员起来。2) Cytokines help the T-cell mature. = 细胞因子帮助 T 细胞成熟。3) The MHC-antigen complex activates the T-cell receptor and the T cell secretes cytokines. = MHC-抗原复合物激活 T 细胞受体，T 细胞随即分泌细胞因子。4) Some cytokines spur the growth of more T-cells. = 一些细胞因子促使更多 T 细胞增殖。5) Some T-cells become cytotoxic cells and track down cells infected with viruses. = 一些 T 细胞变成细胞毒性细胞，去寻找被病毒感染的细胞。6) Some T-cells become helper cells and secrete some cytokines that attract fresh macrophages, neutrophils, other lymphocytes, and other cytokines to direct the recruits once they arrive on the scene. = 一些 T 细胞变成辅助性 T 细胞，分泌细胞因子吸引巨噬细胞、中性粒细胞和其他淋巴细胞，并引导它们到达后的行动。7) Infected cells = 被感染的细胞。图意：箭头自上而下串起 T 细胞被激活、成熟、增殖，并分化成细胞毒性 T 细胞和辅助性 T 细胞的全过程。',
    'dihybrid': '图中文字为库尔德语（库尔曼吉方言），括注为英语。逐条翻译如下：标题「Lêkdana coteduregbûn（dihybrid cross）= 两对相对性状的杂交」；「Lêkdana yekem（first cross）= 第一次杂交」；「♂ = 雄性」「♀ = 雌性」；「Bavan (P)（Parent）= 亲本」；「Rûxsarebabet（phenotype）= 表现型」；「Bomawebabet（genotype）= 基因型」；「Tovê zer û lûs = 黄色圆滑的种子」；「Tovê kesk û qurmiçî = 绿色皱缩的种子」；「AABB Yekreg = AABB 纯合」；「aabb Yekreg = aabb 纯合」；「(2n) = 二倍体」；「(n) = 单倍体」；「Dabeşbûna miyozi（meiosis）= 减数分裂」；「Gamet (G)（gametes）= 配子」；「Xaneyên hêkê（egg cells）= 卵细胞」；「sperm（sperm）= 精子」；「pîtîn（fertilization）= 受精」；「Weçeyên nifşa yekem (F1) = 第一代子代(F1)」；「= %100 Tovên zer û lûs = 100% 黄色圆滑种子」；「= %100 AaBb Dureg（hybrid）= 100% AaBb 杂合(杂种)」；「Lêkdana duyem. Perîna xweyî ya F1ê（second cross. Self pollination of F1）= 第二次杂交，F1 自花授粉」；「Bavan (F1)（Parent）= 亲本(F1)」；「Gamet (G)（gametes）= 配子」；「Weçeyên nifşa duyem (F2) = 第二代子代(F2)」；「Rûxsarebabet: zer û lûs, zer û qurmiçî, kesk û lûs, kesk û qurmiçî = 表现型：黄圆、黄皱、绿圆、绿皱」；「Rêjeya rûxsarebabetê: 9 : 3 : 3 : 1 = 表现型比例 9:3:3:1」；「Hejmara bomawebabetê: 9 cor = 基因型数目 9 种」。左图是两对性状杂交的过程图：亲本黄圆(AABB)与绿皱(aabb)各产生 AB、ab 两种配子，受精后 F1 全为 AaBb 黄圆；右图是 F1 自交的棋盘格（庞纳特方格），行头为雌配子、列头为雄配子，均为 AB、Ab、aB、ab，16 格逐格写出基因型与概率。全图说明自由组合定律：F2 表现型按 9:3:3:1 分离，共 9 种基因型。图中没有坐标轴。',
    'dna_replication': '本说明依据 webui/ui/vendor/bio/dna_replication.gif（512×342，共 40 帧）写成：开头所述画面是动画的第 1 帧。图里的文字全是单个英语字母，逐条列出（原文 = 中文）：原文 P = P（青色小圆圈里的字母，位于 DNA 骨架线上）；原文 S = S（橙色五边形里的字母，位于骨架下方）；原文 A = A、原文 T = T、原文 G = G、原文 C = C（四种碱基的字母，成对写在中间的横档上）。第 1 帧从左到右的配对依次是 A 对 T、T 对 A、G 对 C、A 对 T、A 对 T、G 对 C、T 对 A、C 对 G，上下两个字母方向相反。画面最左边还有一个紫色的钥匙形小图形，那是个图标，不是文字。动画一开始是一条平直的双链：上下各一条由 P 和 S 交替连成的骨架，中间是一排配对的横档。随后双链从中间某处解开，扭成一个叉形（第 9、17 帧能明显看到 Y 字形的分叉和散开的单链），新的核苷酸按配对规则贴到两条分开的模板链上。叉口继续向两边移动、缺口被补齐，到第 24 至 40 帧画面又恢复成两条完整平直的双链，各自带一套 P、S 骨架和一排碱基字母，表示复制完成。全片没有任何成词的英文标注。',
    'dna_structure': '本说明依据 webui/ui/vendor/bio/dna_structure.gif（290×477，共 120 帧）写成：开头所述画面是动画的第 1 帧。逐帧核对后确认，整段动画没有任何文字、字母、数字或图例，所以没有「原文 = 中文」可以列。画面里是一个用球棍模型画出的 DNA 双螺旋：两条由小球串成的长链互相盘绕成螺旋，链外侧的橙红色小球常常成对地向外伸出，绿色小球连成环状的骨架，中间横着一对对深蓝色的长条，成对的深蓝长条之间用浅色细线相连，灰白色的小球散布在四周。因为这些颜色在图中没有任何说明文字，这里只按位置和形状描述，不替它命名。动画的内容就是让这个模型缓慢地绕竖直轴转动：第 1 帧能看到较多正对观众的碱基对，转到第 49、96 帧附近时两侧骨架几乎正对观众、看到的是螺旋的侧棱，第 120 帧又回到接近起始的角度。整个过程中模型本身没有拆开或重组，也没有出现任何步骤、阶段名称。',
    'enzyme_barrier': '原图英文，且文字已经被转成矢量轮廓（源文件里搜不到任何文本节点），下面靠直接看图逐条确认：PE: Ptential Energy (kj) = 势能（千焦），纵轴标题，原文把 Potential 误拼成 Ptential、kj 也写成小写；No enzyme = 不加酶（橙色实线，图例第一行）；+ enzyme = 加酶（红色虚点线，图例第二行）；activation energy = 活化能；G of reactants = 反应物的自由能；G of Products = 产物的自由能；ΔG = 自由能变化；Reaction Coordinate = 反应坐标（横轴标题）。图意：横轴是反应进程、纵轴是能量。橙色实线表示不加酶时反应必须先翻过一座很高的活化能山峰；红色虚点线表示加酶以后这座山峰明显变矮，反应容易发生。两条曲线的起点（反应物）和终点（产物）高度相同，所以右侧用双向箭头标出的 ΔG 在两种情况下完全一样——酶只降低活化能，并不改变反应前后的自由能差。',
    'enzyme_conditions': '原图波兰文，文字同样已被转成矢量轮廓，靠看图逐条确认：AKTYWNOŚĆ = 酶活性（纵轴标题，竖排文字）；OPTIMUM = 最适（点），图正中一条暗红色竖线所指的曲线最高处；DENATURACJA = 变性，沿曲线陡降那一段斜排的文字；TEMPERATURA = 温度（横轴标题）。图意：这条曲线表示酶活性随温度的变化关系。温度从低到高时，酶活性先缓慢上升、再加速升高，在最适温度处达到峰值，图上用一条竖线把这个点标成 OPTIMUM；超过这个温度之后活性急剧下降，曲线几乎直线跌落，陡降段旁边写着 DENATURACJA，说明高温使酶变性而失活。图里只有这四个标注，没有画出任何数字刻度、单位或具体的温度与活性数值。',
    'expression_vector': '原文英语。逐条翻译：Active Repressor = 有活性的阻遏蛋白；Inactive Repressor = 失活的阻遏蛋白；RNA Polymerase (Transcription Enzyme) = RNA 聚合酶（转录酶）；Gene of Interest = 目的基因；+ Inducer Molecule (IPTG) = 加入诱导物分子（IPTG）；Repressor binds to promoter and blocks transcription of the gene of interest by preventing transcription machinery from attaching to the promoter to initiate transcription. = 阻遏蛋白结合在启动子上，使转录机器无法结合启动子起始转录，从而阻断目的基因的转录；Inducer molecule binds to the repressor and deactivates it. This allows for the transcription machinery to access the plasmid at the promoter and the gene of interest will be transcribed. = 诱导物分子结合阻遏蛋白并使其失活，转录机器得以在启动子处进入质粒，目的基因随之被转录。图分左右两半，左边是关、右边是开：左边橙色的阻遏蛋白占着质粒的启动子，蓝色的 RNA 聚合酶靠近不了，紫色的目的基因不表达；中间的大箭头表示加入 IPTG，右边 IPTG（绿色小圆）结合阻遏蛋白、使它脱离启动子，RNA 聚合酶随即结合启动子并转录目的基因。这张图讲的是诱导型质粒的开关原理：阻遏蛋白是开关，IPTG 是钥匙，没有诱导物时基因关闭，加了诱导物基因才打开，所以能人为控制目的基因什么时候表达。质粒上还有黄色片段和黑色小片段，图上没给它们文字标注。',
    'gene_transfer': '这是动画 GIF，共 24 帧（抽样核对了 12 帧）。图中文字是英文，共 4 处：nucleoid = 拟核；bacterial chromosome = 细菌染色体（原文带括号写作 (bacterial chromosome)，紧排在 nucleoid 下方）；plasmid = 质粒；bacterial cell = 细菌细胞。画面：中央是一个浅蓝色、外描深蓝边的长杆状细菌细胞，里面有一团金黄色缠绕细线，用引线标 nucleoid 与 (bacterial chromosome)；画面左侧一个小金黄色圆环，用引线标 plasmid；细胞右下方用引线标 bacterial cell。首帧里质粒离细胞较远，逐帧看它慢慢向细菌移动，最后贴到细胞的左缘、画在细胞轮廓之内，表示质粒进入细菌细胞。这 4 处英文标注在所有帧里的文字和位置都保持不变，画面中没有其它文字。',
    'humoral_immunity': '原图文字为英文，同一位置还内置了孟加拉文、加泰罗尼亚文、捷克文、法文、德文、库尔德文、马其顿文、土耳其文、加利西亚文、立陶宛文、俄文、阿萨姆文、泰文、希腊文、保加利亚文等 16 种语言，按英文逐条翻译：Antigens = 抗原（复数）；Antigen = 抗原；Antigen-binding site = 抗原结合位点；Antibody = 抗体。要特别指出：这四组标注每一组都没有“无语言”的兜底文本，所以浏览器语言是中文时一条都不会显示，中文环境下看到的就是一张没有任何文字的图，只剩图形。图形内容：右上角一排六个彩色小块（紫、红、绿、黄、蓝、土黄）代表不同的抗原；下方是一个 Y 形的抗体，两条臂的主体是淡蓝色、臂末端为黄色；左上臂顶端有一小块黄色三角形是抗原，一根细箭头从 Antigen 指向它与抗体相接的部位，也就是抗原结合位点。图中没有细胞、也没有任何表示免疫过程先后顺序的箭头。',
    'independent_assortment': '这是动画 GIF，共 33 帧（抽样核对了 12 帧）。图中文字是意大利语，共 5 处：CAR. 1 = 第 1 号（卡片上第一列的缩写）；CAR. 2 = 第 2 号（第二列）；COLORE = 颜色；FORMA = 形状；INCROCIA = 杂交（交配）。画面结构：上方两张橙黄色卡片是两个亲本，每张卡片都分 COLORE（颜色）与 FORMA（形状）两行，每行在 CAR. 1、CAR. 2 两列各画一个标记——颜色是黄色圆或绿色圆，形状是白色光滑圆或白色皱缩圆；卡片下方左边一个黄色光滑的圆球、右边一个绿色皱缩的圆球，两者中间是一个写着 INCROCIA 的按钮；最下方另有一张子代小卡片和一个圆球。逐帧抽样看到：橙红色或粉红色的光圈会分别套在颜色、形状的圆圈上，像是在指示这一步取的是哪个性状；下方子代卡片与子代圆球的颜色、形状组合随抽取结果改变。全部文字只有上面 5 处，没有其它标注。',
    'internal_environment': '这张图里没有任何文字，也没有一个可翻译的标注，唯一的“字”是六个编号数字：1、2、3、4、5、6（蓝色圆形里的阿拉伯数字，对应从上到下的六行）。逐行说明图上画的东西：每行左边三个粗箭头表示方向组合，第 1 行是下、平、平（两条短横线表示不变），第 2 行是下、上、下，第 3 行是上、平、平，第 4 行是上、下、上，第 5 行是上、上、下，第 6 行是下、下、上；每行右边是一个蓝色长方形，中央有一条竖直轴，轴顶端一个向上的箭头，底边两端各一个向外的水平箭头，另用一个红色虚线框画出同一长方形的另一个位置和大小（可理解为变化前或变化后的对照轮廓）。就图形本身只能读出这些：它把三个方向量的变大、不变、变小穷举成六种组合，每种组合配一个实线黑框与一个红色虚线框对照，说明横竖尺寸随组合改变。原图没有写任何名称，所以不能从图上确定这三个量分别指什么，也不能确定虚线框画的是变化前还是变化后的状态。',
    'light_reaction': '本说明依据 webui/ui/vendor/bio/light_reaction.gif（250×300，共 6 帧）写成：开头所述画面是动画的第 1 帧。图里的标注全是英语，逐条列出（原文 = 中文）：Energy = 能量；Carbon dioxide = 二氧化碳；Water = 水；Chlorophyll = 叶绿素；Oxygen is released = 释放出氧气；Glucose is formed = 生成葡萄糖；Photosynthesis = 光合作用（写在底部的青色横条上）。画面左侧有一株橙色茎、青色叶片的植株，根扎在灰褐色的土里，土中画着一条浅蓝色的水层；左上方黄色的圆（太阳）向植株射出三个箭头，标着 Energy；左侧和下方的黑色箭头分别标着 Carbon dioxide 与 Water，都指向植株；茎上写着 Chlorophyll；从叶片向右上方去的箭头标 Oxygen is released，向右下方去的箭头标 Glucose is formed。6 帧之间这几组箭头的明暗在交替变化：第 1 帧所有箭头都是黑色；之后的帧里，有时太阳和二氧化碳的箭头是黑色而氧气、葡萄糖的箭头变灰，下一帧又反过来，表示光能和二氧化碳的进入与氧气、葡萄糖的产生在轮流被强调。',
    'meiosis': '本说明依据 webui/ui/vendor/bio/meiosis.gif（1200×675，共 21 帧）写成：开头所述画面是动画的第 1 帧。逐帧核对后确认，全片没有任何文字、字母或数字标注，所以没有「原文 = 中文」可以列。第 1 帧也是一个细胞：灰色的细胞质里画着核的轮廓，核内是蓝、紫、橙黄三色细丝缠绕成的团块。之后细丝缩短变粗成棒状染色体，画面中出现四条两两成对的染色体（蓝色、橙色、紫色各一对），它们排到细胞中央的虚线椭圆上，随后成对的染色体彼此分开，各移向细胞的一极。细胞第一次从中部缢缩、断成两个细胞，每个细胞里的染色体数目减半。紧接着这两个细胞各自又把染色体排到中央，X 形的染色体从中间分开、两半分别移向两极，两个细胞再次缢缩，最终形成四个细胞。最后一帧里四个子细胞核内的染色体又松散成细丝、重新缠绕成团，表示分裂结束。',
    'membrane_model': '这是动画 GIF，共 28 帧（抽样核对了 12 帧）。图上出现的文字只有一处，是意大利语：EFFETTI = 效应（效果），蓝色大写字母，只出现在最后几帧的底部；第 1 帧（判定用首帧）没有任何文字，全部帧里也没有别的词。画面：中间一条黄绿色横带代表膜，膜上方是浅色区域，下方是浅蓝色区域；膜上嵌着一个橙色的大块结构，上部像两片竖起的片、中部膨大成球状，从它的下部向左右两侧各伸出一串红色圆球，越往外球越小，像沿着通道排成的链。逐帧看，一个带小尾巴的绿色气泡从画面上方慢慢靠近橙色结构并落在它上面；到最后几帧，两串红色圆球的末端各出现一个深红色箭头指向外下方，同时底部显示 EFFETTI。除这一个词之外，全图没有其它标注文字。',
    'mitosis': '本说明依据 webui/ui/vendor/bio/mitosis.gif（1200×675，共 12 帧）写成：开头所述画面是动画的第 1 帧，其余各帧为按顺序抽看的画面。逐帧核对后确认，这段动画里没有任何文字、字母或数字标注，所以没有「原文 = 中文」可以列。第 1 帧里是一个圆形细胞：外圈灰色区域是细胞质，中间用细线画出细胞核的轮廓，核内是蓝、紫、橙黄三种颜色的细长丝状物互相缠绕成一团。随后的帧里这团细丝逐渐缩短、变粗，成为一条条棒状或 X 形的染色体，蓝色、紫色、橙黄色各成一对。接着染色体排到细胞中央的虚线圈上，每一条从中间分开，两半分别被拉向细胞的两端。细胞再从中间向内凹陷、缢缩，最后完全断成两个子细胞，每个子细胞各有一圈核膜和一组染色体；到最后一帧，两个子细胞核里的染色体又重新松开、变回细长丝状的缠绕形态，循环回到起始的样子。',
    'monoclonal_antibody': '这张图和 humoral_immunity.svg 是同一个文件（两个文件字节完全相同），所以标注与图形一模一样。原图文字为英文，并内置 16 种语言，按英文逐条翻译：Antigens = 抗原（复数）；Antigen = 抗原；Antigen-binding site = 抗原结合位点；Antibody = 抗体。同样地，这四组标注都没有“无语言”的兜底行，浏览器语言是中文时这些字全部不显示，只剩图形。图形内容：右上角六个彩色小块代表不同抗原；下面是一个 Y 形抗体，淡蓝色为主体、两条臂末端黄色；左臂顶端画着黄色三角形状的抗原，一根细箭头从 Antigen 指向它与抗体结合的部位，即抗原结合位点。虽然文件名是单克隆抗体，但图里并没有画单克隆抗体的制备过程，也没有细胞、培养皿、杂交瘤之类的内容，只有抗原与抗体结合这一层关系。',
    'natural_selection_freq': '这是动画 GIF，共 16 帧（抽样核对了 12 帧），画面里没有任何文字，没有可翻译的图内文字。首帧：绿色草地上有两扇长满眼状斑纹的绿色尾屏，尾屏前各站一只蓝身孔雀，右侧一只褐色、没有尾屏的雌孔雀正朝它们走近。抽样看到的过程是：雌孔雀与孔雀靠在一起，其中一帧在两只鸟上方出现一颗红心；另一帧在一只孔雀身上盖了红色禁止符号（红圈加斜杠），像是表示这个个体不能参与繁殖；再往后画面里出现尾屏更小、数量更多的孔雀个体，草地上鸟的数量和尾屏大小前后并不一样。文件名是 natural_selection_freq（自然选择频率），画面用孔雀数量和尾屏的变化演示群体随代次的改变，但全图没有出现任何字母或数字，含义完全靠图形表示。',
    'nerve_conduction': '【一、图中的文字】全片标注只用英文一种语言。三帧顶部都写 “Outside of cell” = 细胞外，底部都写 “Inside of cell” = 细胞内。右侧说明文字随帧更换：第 1 帧一行 “1 - Cell at rest” = 1 - 细胞处于静息状态；第 2 帧四行 “2 - Stimulus triggers” = 2 - 刺激触发、“action potential” = 动作电位、“Ions pass” = 离子通过、“through membrane” = 穿过膜；第 3 帧两行 “3 - Action potential” = 3 - 动作电位、“spreads” = 扩散、传开。画面里的 “+” 和 “−” 只是电荷符号，不是文字；黑色箭头、红色竖线、蓝色和绿色弧线同样是图形，不是文字。\n\n【二、这个动画在演什么】整张图共三帧，每帧停两秒、依次切换。上下两排并排的黄色圆圈中间留出一条缝当膜。第 1 帧膜外一侧标 “+”、膜内一侧标 “−”，是外正内负的静息状态。第 2 帧在中间一处冒出朝上、朝下的黑色箭头和穿过两层膜的红色竖线，该处符号翻成膜外 “−”、膜内 “+”，配文说刺激引起了动作电位、离子穿过膜。第 3 帧里膜外的蓝色弧线和膜内的绿色弧线成对弯向相邻部位，正负号在中间一带继续颠倒，演的是膜电位变化顺着膜向两侧扩布、兴奋传播开去。',
    'phage_experiment': '图中文字均为英文。逐条翻译如下（保持原图拼写，含原图的语法错误）：「Phage introduces DNA into bacteria. = 噬菌体把 DNA 注入细菌。」；「Low levels of Acr. = Acr 水平低。」；「CRISPR protective system is effective. = CRISPR 防御系统有效。」；「High levels of Acr. = Acr 水平高。」；「CRISPR protective system is inhibit. = CRISPR 防御系统被抑制。」；「Successful phage infection = 噬菌体感染成功」；「Lysogeny = 溶原化」；「Bacterial-DNA = 细菌 DNA」；「CRISPR-cas = CRISPR-cas 系统」；「Acr = 抗 CRISPR 蛋白」；「Pseudomonas aeruginosa = 铜绿假单胞菌」；「Lytic replication = 裂解性复制」。上下两个面板对比同一实验：都以铜绿假单胞菌为宿主，菌内标出 CRISPR-cas 与细菌 DNA，噬菌体在菌外吸附后把红色线条表示的 DNA 注入菌内。上图 Acr 水平低，CRISPR-cas 能识别并切断入侵的噬菌体 DNA（红色叉号），防御有效，感染失败；下图 Acr 水平高，Acr 蛋白挡住 CRISPR-cas，噬菌体 DNA 得以保留，可整合进细菌 DNA 进入溶原化，也可大量复制并裂解细菌，释放出许多子代噬菌体。图意说明噬菌体靠 Acr 蛋白对抗细菌的 CRISPR 免疫，Acr 表达量高低决定感染被阻断还是走向成功。图中没有坐标轴、时间轴或单位。',
    'population_growth': '「原文 = 译文」图中文字为英文：Population (as a percentage of carrying capacity) = 种群数量（以环境容纳量的百分比表示）；Time = 时间；纵轴刻度 0、50、100 = 0%、50%、100%；横轴刻度 0、1、2、3、4、5 = 时间单位。「图意」这是一条典型的 S 形（逻辑斯谛）增长曲线。蓝色曲线从时间 0 附近、约 15% 容纳量的低位起步，起初上升较慢，在时间 1 到 2 之间最陡，说明此时种群增长最快；此后增速逐渐放慢，到时间 3 以后曲线几乎变成水平，稳定在 100% 处不再上升。100 处的水平线就是环境容纳量，表示种群数量达到环境所能承载的上限后停止增长。整图说明在资源有限的环境中，种群先加速增长、再减速、最终稳定；图中只有这一条数据曲线，没有第二条曲线或其他标注。',
    'protein_structure': '原文英语。逐条翻译：Model Confidence: = 模型置信度；Very high (pLDDT > 90) = 非常高（pLDDT 大于 90）；Confident (90 > pLDDT > 70) = 可信（pLDDT 在 70 到 90 之间）；Low (70 > pLDDT > 50) = 低（pLDDT 在 50 到 70 之间）；Very low (pLDDT < 50) = 非常低（pLDDT 小于 50）；P28:P32 = 第 28 到第 32 位残基；P100:P103 = 第 100 到第 103 位残基；WWBD&DB = WWBD 与 DB 结构域。图是一条蛋白质链的三维带状模型，颜色按预测置信度上色：深蓝最好、浅蓝次之、黄色较低、橙红最低，右上角图例给出这四档的分界线。pLDDT 是结构预测模型给出的逐残基置信度分数，分数越高说明模型越有把握，颜色越偏深蓝；分数低的地方（黄、橙、红）往往是柔性大、没有固定构象的区域。深蓝色的带状段表示模型很确定的螺旋和折叠，黄橙色的细线表示模型不确定的松散区。图中用黄色方框和绿色虚线框圈出了三段被特别指出的区域，分别是 P28:P32、P100:P103 和 WWBD&DB。整张图只说明一条蛋白质链的折叠形态和模型对各部位的把握程度，图上的方框只是区域提示，框线本身不带文字，整张图也没有坐标轴，没有标出一级、二级、三级、四级结构这些名词。',
    'resting_potential': '「原文 = 译文」图中只有符号与序号：1. = 第 1 步；2. = 第 2 步；3. = 第 3 步；K⁺ = 钾离子；An⁻ = 阴离子符号；K⁺An⁻ = 钾盐（钾离子与阴离子组成的盐）。「图意」图用三个圆圈代表同一个细胞（膜内的区室），三者之间用粗箭头连成 1→2→3 的先后顺序。第 1 步圆圈内只写着 K⁺An⁻，表示膜内外还没有离子跨膜移动；第 2 步细胞内的钾离子开始顺浓度差向外扩散，绿色箭头指向圆外并标出 K⁺，同时有一个较短的红色箭头指向圆内；第 3 步绿色外流箭头与红色内流箭头长度接近、方向相反，表示钾离子向外扩散与向内回流达到动态平衡，膜两侧的电位差随之稳定不再变化。整图说明静息电位主要由钾离子外流形成并维持在平衡状态；除上述符号外图中没有其他文字。本文件与 potential_curve.jpg 字节完全相同，是同一张图。',
    'restriction_enzyme': '原文英语。逐条翻译：DNA = 脱氧核糖核酸；EcoRV = EcoRV（一种限制酶的名字）；EcoRV binds loosely = EcoRV 松散结合；if cognate sequence = 如果是它识别的同源序列；kink = 扭折、弯折；EcoRV kinks DNA = EcoRV 使 DNA 扭折；cleavage = 切割。整张图是一条自上而下的流程：最上面红色双螺旋是 DNA，紫色团块是 EcoRV 酶分子；酶先与 DNA 松散结合，中间的黑色双向箭头表示这种结合可逆、可以解离；只有遇到同源序列（图上那行斜体字）才继续向下——酶把 DNA 折出一个扭折，两侧的黑色小弧线箭头表示 DNA 被弯折；最后一步是切割，DNA 在结合处被切断成两段，酶在最下方重新画成一个完整分子。紫色团块表面凹凸不平，表示酶与 DNA 接触的结合面；红色双螺旋上的灰色部分是暴露出来的碱基。每一步之间都是黑色单向箭头，表示先后顺序；中间的黑色双向箭头是唯一表示可逆的符号。图上只有这七个英文标注，没有出现识别序列、黏性末端、磷酸二酯键之类的字样，整张图只讲这一个酶切 DNA 的过程。',
    'species_relation': '原图是多语言文件（含德文、英文、法文、古吉拉特文、俄文、卡纳达文、马拉雅拉姆文、泰卢固文、印尼文共 9 种语言），除猎物那一组以外都有英文兜底，所以中文浏览器显示的是英文。逐条翻译：Population = 种群数量（德文 Populationsgröße，纵轴标题）；Time = 时间（德文 Zeit，横轴标题）；Predator = 捕食者（德文 Räuberpopulation，红色文字）；Prey = 猎物（德文 Beutepopulation，绿色文字）。特别要指出猎物这一组：它在源文件里的兜底文本写的是占位符 “xxx”，并没有真正的英文兜底，所以中文浏览器下这个位置的的确确显示三个字母 xxx。图意：这是捕食者与猎物种群数量随时间变化的曲线图，绿色填充的是猎物种群，黄褐色填充的是捕食者种群；两条曲线都呈周期性起伏且不同步，猎物先到达高峰、捕食者随后才升高，表现出典型的滞后波动关系。图中没有纵轴数字刻度，也没有标出任何具体数值。',
    'synapse': '这张图上没有任何文字标注，也没有箭头，整幅画只有图形，所以没有可翻译的标注。逐处说明图上画了什么：上部蓝色的粗线是突触前神经元的轴突末梢，画成膨大的球形末端，蓝线内包着粉红色的区域；末梢最上面还画了两个星形的小图形；末梢底边有三处向下凸起的凹陷，每个凹陷里都有一小簇红色的圆球；末梢下方是一条淡黄色的带状缝隙，缝隙里散布着许多单独的红色小圆球；最下面是灰紫色的一大块，即突触后膜。按图形的读法：红色小圆球代表突触小泡里的神经递质，它们从突触前膜一侧释放出来，穿过中间那条淡黄色缝隙扩散过去，最后停在灰紫色的突触后膜一侧。全图没有一个字母，也没有线粒体、受体、髓鞘之类的画法，画里没有标注方向，只能靠小泡释放的位置和递质的分布判断传递方向是从上往下，因此这条只能描述图形本身，不存在逐词翻译。',
    'thyroid_axis': "原图德文。逐条翻译：Hypothalamus = 下丘脑；TRH = 促甲状腺激素释放激素；Adeno-hypophyse = 腺垂体；TSH = 促甲状腺激素；Schilddrüse = 甲状腺；T₃ = 三碘甲状腺原氨酸；T₄ = 甲状腺素；5'-Dejodase = 5'-脱碘酶；Peripherie = 外周（组织）；红圈里的加号 = 促进；黑圈里的减号 = 抑制。顺着红色箭头看：下丘脑分泌 TRH 作用于腺垂体（加号），腺垂体分泌 TSH 作用于甲状腺（加号），甲状腺合成并分泌 T₃ 和 T₄；T₄ 到达外周后被 5'-脱碘酶转变成 T₃。黑色折线是负反馈回路：图上有三处减号，分别落在通往下丘脑的点线、通往腺垂体的实线和外周那一端，表示血液中 T₃、T₄ 升高时反过来抑制下丘脑和腺垂体，使 TRH、TSH 减少。这张图讲的是下丘脑、腺垂体、甲状腺三级的分级调节：上级激素推动下级腺体，最下级的激素又反过来压住上面两级，形成闭环，所以激素水平能稳定在正常范围。三个层级从上到下依次是下丘脑、腺垂体、甲状腺，最下面一格是外周组织。全图正文只有上述九个标注词，没有出现代谢、肿大之类的字样。",
    'transcription': "本说明依据 webui/ui/vendor/bio/transcription.gif（512×342，共 123 帧）写成：开头所述画面是动画的第 1 帧。图里标注全是英语，逐条（原文 = 中文）：TRANSCRIPTION = 转录；(in nucleus) =（在细胞核内）；TRANSLATION = 翻译；tRNA's bring amino acids = tRNA 运来氨基酸；PEPTIDE BOND FORMATION = 肽键形成（红字）；(in cytoplasm) =（在细胞质内）；THE PROCESS IS COMPLETE! = 过程完成！；THE POLYPEPTIDE WILL COMBINE WITH OTHER POLYPEPTIDES TO BECOME A PROTEIN. = 这条多肽将与其他多肽结合成为蛋白质。氨基酸名：methionine = 甲硫氨酸、arginine = 精氨酸、histidine = 组氨酸、threonine = 苏氨酸、cysteine = 半胱氨酸、asparagine = 天冬酰胺、tryptophan = 色氨酸。前 50 帧在核内：双链局部解开，游离核苷酸按碱基配对贴到模板上，排成一条新链。第 50 帧后切到细胞质，核糖体与几种氨基酸出现，按上述顺序连成一条链，即肽键形成。最后一帧多肽卷曲折叠成一团。",
    'translation': '本说明依据 webui/ui/vendor/bio/translation.gif（678×346，共 81 帧）写成：开头所述画面是动画的第 1 帧。图里的文字全是英语字母和一条碱基序列，逐条列出（原文 = 中文）：mRNA 上的序列 AGCGAUGGACUGAAACCCUAUCCUGGAUGAAACCC = （同一串碱基序列，其中 UGA 三个字母用红色印出）；tRNA 分子旁的三个字母标签 GAG = GAG、CCU = CCU、GAC = GAC、GGG = GGG、UUU = UUU、UCG = UCG、CUA = CUA、AUA = AUA、GGA = GGA、ACU = ACU。这些都是碱基或密码子字母本身，图里没有写出它们的中文名称；另外第 49 帧起有一个粉色椭圆写着 ACU，同样只是字母。动画开始时核糖体（黄色圆形，分上下两半）夹住 mRNA，带彩色小球（氨基酸）的 tRNA 按三个字母与 mRNA 上相邻的三个字母配对，一个一个进入核糖体；进入的氨基酸彼此连接、从核糖体上方伸出一条越来越长的链（第 17、33、49、65 帧能看到链逐渐变长、小圆点变多），核糖体则沿 mRNA 向右移动。当红色的 UGA 被读到，反应停止：最后一帧只剩一条 mRNA、空出来的核糖体亚基和几个漂开的 tRNA。',
}


def _clean_chapter(name):
    return "选必1" if name == "选备1-传导" else name


def processes(chapter=None):
    """过程表（可按章节筛）。"""
    rows = []
    for key, chapter_name, name, kind, curve, steps in PROCESSES:
        if chapter and _clean_chapter(chapter_name) != _clean_chapter(chapter):
            continue
        rows.append({"key": key, "chapter": _clean_chapter(chapter_name), "name": name,
                     "kind": kind, "steps": list(steps), "media": media(key),
                     "media_note": MEDIA_NOTES.get(key, ""),
                     "show_figure": (key in MEDIA_PLUS_ANIM) or not media(key)})
    return rows


def chapters():
    seen = []
    for key, chapter_name, name, kind, curve, steps in PROCESSES:
        name2 = _clean_chapter(chapter_name)
        if name2 not in seen:
            seen.append(name2)
    return seen


def process(key):
    for row in processes():
        if row["key"] == str(key):
            return row
    raise ValueError("没有这个过程：%s" % key)


# ============================ 通用画法 ============================
# 六种 kind 各画一套：chain 链式、cycle 循环、membrane 膜/电位、curve 曲线、
# grid 棋盘格、tree 分支。当前阶段（index）用高亮/游标标出来，图下方带一行阶段文字。

W, H = 660, 380
BLUE, GREEN, ORANGE, PURPLE, RED = "#2f6fd0", "#2f9e6b", "#d98324", "#8257c8", "#c0392b"


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(text, per=13, lines=3):
    text = str(text)
    rows = []
    while text and len(rows) < lines:
        rows.append(text[:per])
        text = text[per:]
    if text and rows:
        rows[-1] = rows[-1][:-1] + "…"
    return rows


def _text(x, y, value, size=12, color="#3a4652", anchor="middle", bold=False):
    return ('<text x="%g" y="%g" font-size="%g" fill="%s" text-anchor="%s"%s>%s</text>'
            % (x, y, size, color, anchor, ' font-weight="bold"' if bold else "", _esc(value)))


def _box(x, y, w, h, label, on, color=BLUE):
    """一个圆角标签框；on=True 表示"这就是当前阶段"。"""
    parts = ['<rect x="%g" y="%g" width="%g" height="%g" rx="7" fill="%s" stroke="%s" '
             'stroke-width="%g"/>' % (x, y, w, h, color if on else "#f4f7fb",
                                      color if on else "#cdd7e2", 2.2 if on else 1)]
    rows = _wrap(label, per=max(4, int(w / 12.5)))
    top = y + h / 2 - (len(rows) - 1) * 8.5 + 4.5
    for order, row in enumerate(rows):
        parts.append(_text(x + w / 2, top + order * 17, row, 12,
                           "#ffffff" if on else "#3a4652"))
    return "".join(parts)


def _arrow(x1, y1, x2, y2, on, color=BLUE):
    """带箭头的连线（用极简三角头，不引 marker）。"""
    import math
    ink = color if on else "#b9c4d0"
    wide = 2.2 if on else 1.2
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 7
    wing = 0.42
    head = [(x2, y2),
            (x2 - size * math.cos(angle - wing), y2 - size * math.sin(angle - wing)),
            (x2 - size * math.cos(angle + wing), y2 - size * math.sin(angle + wing))]
    return ('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%g"/>'
            '<polygon points="%s" fill="%s"/>'
            % (x1, y1, x2 - 4 * math.cos(angle), y2 - 4 * math.sin(angle), ink, wide,
               " ".join("%g,%g" % point for point in head), ink))


def _caption(steps, index, bottom=None):
    text = steps[max(0, min(index, len(steps) - 1))] if steps else ""
    rows = _wrap("第 %d 步：%s" % (index + 1, text), per=44, lines=2)
    top = (H - 30) if bottom is None else bottom
    parts = []
    for order, row in enumerate(rows):
        parts.append(_text(W / 2, top + order * 16, row, 12, "#2b3a46"))
    return "".join(parts)


def _head(title):
    return ('<svg class="bio-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" data-bio="1"><rect width="%d" height="%d" fill="#fff"/>'
            % (W, H, W, H, W, H) + _text(W / 2, 22, title, 13, "#5b6773"))


# ---------------- 动画（SVG 声明式动画，内核 109 原生支持，不需要 JS 驱动） ----------------
# 老师要的是"仿真动画"不是静图：所以每张图里都有东西在动——物质沿着过程流动、
# 分子穿过膜、游标沿曲线走、配子飞进棋盘格、染色体分离。播放/暂停用 svg.pauseAnimations()。

def _pulse(dur=1.8, low=0.4, begin=0.0):
    return ('<animate attributeName="opacity" values="1;%g;1" dur="%gs" begin="%gs" '
            'repeatCount="indefinite"/>' % (low, dur, begin))


def _motion(path_d, dur=2.4, begin=0.0):
    return ('<animateMotion dur="%gs" begin="%gs" repeatCount="indefinite" path="%s"/>'
            % (dur, begin, path_d))


def _dot(path_d, dur=2.4, begin=0.0, r=7, color=ORANGE, label=None):
    """一个沿着 path 一直跑的小球（代表流过去的物质/能量）。"""
    parts = ['<g><circle r="%g" fill="%s" stroke="#ffffff" stroke-width="1.4">%s</circle>'
             % (r, color, _motion(path_d, dur, begin))]
    if label:
        parts.append(_text(0, 4, label, 9, "#ffffff"))
    return "".join(parts) + "</g>"


def _path_of(points):
    return "M " + " L ".join("%g %g" % (x, y) for x, y in points)


def _ellipse_path(cx, cy, rx, ry):
    return ("M %g %g A %g %g 0 1 0 %g %g A %g %g 0 1 0 %g %g Z"
            % (cx - rx, cy, rx, ry, cx + rx, cy, rx, ry, cx - rx, cy))


def _breath(color=ORANGE, dur=1.6):
    """给"当前这一步"的元素用：一亮一暗，表示"就是这里"。"""
    return ('<animate attributeName="opacity" values="1;0.45;1" dur="%gs" '
            'repeatCount="indefinite"/>' % dur)


def _chain(steps, index):
    count = len(steps)
    cols = count if count <= 4 else 3
    rows_n = (count + cols - 1) // cols
    gap = 22
    box_w = (W - 40 - gap * (cols - 1)) / cols
    box_h = 58
    top0 = 46 if rows_n == 1 else 52
    v_gap = 42 if rows_n > 2 else 62
    parts = []
    spots = []
    for order in range(count):
        row, col = divmod(order, cols)
        if rows_n > 1 and count % cols and row == rows_n - 1:      # 末行居中
            last = count - row * cols
            left = (W - (box_w * last + gap * (last - 1))) / 2
        else:
            left = 20
        x = left + col * (box_w + gap)
        y = top0 + row * (box_h + v_gap)
        spots.append((x, y))
        parts.append(_box(x, y, box_w, box_h, steps[order], order == index))
    for order in range(count - 1):
        (x1, y1), (x2, y2) = spots[order], spots[order + 1]
        on = order + 1 == index or order == index
        if abs(y1 - y2) < 1:
            parts.append(_arrow(x1 + box_w + 3, y1 + box_h / 2, x2 - 3, y2 + box_h / 2, on))
        else:
            parts.append(_arrow(x1 + box_w / 2, y1 + box_h + 3, x2 + box_w / 2, y2 - 3, on))
    # 动画：一颗"物质"沿着步骤往下走，走到哪一步那颗球就亮；当前框在呼吸
    centers = [(x + box_w / 2, y + box_h / 2) for x, y in spots]
    if len(centers) > 1:
        parts.append(_dot(_path_of(centers), dur=max(2.4, count * 1.1), r=8, color=GREEN))
    for order, (x, y) in enumerate(spots):
        if order == index:
            parts.append('<rect x="%g" y="%g" width="%g" height="%g" rx="7" fill="none" '
                         'stroke="%s" stroke-width="2.5" stroke-dasharray="6 5">%s</rect>'
                         % (x - 4, y - 4, box_w + 8, box_h + 8, ORANGE, _pulse(1.4)))
    return _head("过程：一步接一步（小球一路走到底）") + "".join(parts)


def _cycle(steps, index):
    import math
    count = len(steps)
    cx, cy, rx, ry = W / 2, 176, 218, 104
    box_w, box_h = 168, 50
    parts = ['<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="none" stroke="#dbe3ec" '
             'stroke-width="10"/>' % (cx, cy, rx + 6, ry + 6)]
    for order, label in enumerate(steps):
        angle = -math.pi / 2 + order * 2 * math.pi / count
        x = cx + rx * math.cos(angle) - box_w / 2
        y = cy + ry * math.sin(angle) - box_h / 2
        parts.append(_box(x, y, box_w, box_h, label, order == index, GREEN))
        if order == index:
            parts.append('<rect x="%g" y="%g" width="%g" height="%g" rx="7" fill="none" '
                         'stroke="%s" stroke-width="2.5" stroke-dasharray="6 5">%s</rect>'
                         % (x - 4, y - 4, box_w + 8, box_h + 8, ORANGE, _pulse(1.4)))
    # 动画：一颗球绕着圈一直转，圈上的小点跟着亮
    parts.append(_dot(_ellipse_path(cx, cy, rx, ry), dur=7.0, r=9, color=ORANGE))
    return _head("循环：走到头又回到起点（小球在转圈）") + "".join(parts)


def _crosser(x, dur=3.0, begin=0.0, color="#3f9ad9", r=6):
    """一个从膜下穿到膜上的小分子（循环：淡入 -> 穿过去 -> 淡出）。"""
    return ('<g><circle r="%g" fill="%s" stroke="#ffffff" stroke-width="1.2">'
            '<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.12;0.88;1" '
            'dur="%gs" begin="%gs" repeatCount="indefinite"/>%s</circle></g>'
            % (r, color, dur, begin, _motion("M %g 246 L %g 118" % (x, x), dur, begin)))


def _membrane(steps, index, title="膜两侧的物质移动"):
    count = len(steps)
    spots = [(190, True), (470, True), (190, False), (470, False)]
    parts = ['<rect x="30" y="160" width="%d" height="34" fill="#eaf2fb" stroke="#a9c4e2"/>'
             % (W - 60)]
    for spot in range(30, int(W - 30), 26):
        parts.append('<circle cx="%d" cy="168" r="6" fill="#f6d488" stroke="#d9ab3c"/>'
                     '<circle cx="%d" cy="186" r="6" fill="#f6d488" stroke="#d9ab3c"/>'
                     % (spot, spot))
    parts.append('<rect x="262" y="168" width="136" height="19" rx="6" fill="#ffffff" '
                 'opacity="0.92"/>')
    parts.append(_text(W / 2, 182, "磷脂双分子层（膜）", 11, "#4a6b90"))
    for order, label in enumerate(steps):
        x, up = spots[order % 4]
        on = order == index
        color = ORANGE if on else "#8b98a6"
        rows = _wrap(label, per=17, lines=3)
        if up:
            parts.append(_arrow(x, 138, x, 96, on, color))
            top = 92 - (len(rows) - 1) * 16
            for line, row in enumerate(rows):
                parts.append(_text(x, top + line * 16, row, 11.5,
                                   "#a35a12" if on else "#7b8794"))
        else:
            parts.append(_arrow(x, 216, x, 258, on, color))
            for line, row in enumerate(rows):
                parts.append(_text(x, 276 + line * 16, row, 11.5,
                                   "#a35a12" if on else "#7b8794"))
    # 动画：水分子一个个从膜下穿到膜上（净移动方向），当前这步的箭头在呼吸
    for order, spot in enumerate(range(76, int(W - 60), 62)):
        parts.append(_crosser(spot, dur=3.2, begin=round(order * 0.45, 2)))
    for order in range(count):
        if order == index:
            x, up = spots[order % 4]
            parts.append('<circle cx="%g" cy="%g" r="5" fill="%s">%s</circle>'
                         % (x, 90 if up else 262, ORANGE, _pulse(1.2)))
    return _head(title + "（分子在穿膜）") + "".join(parts)


def _mono_grid(steps, index):
    parts = []
    left, top, cell = 210, 76, 74
    parts.append(_text(left + cell, top - 26, "配子", 12, "#5b6773"))
    heads = ["D", "d"]
    for order, head in enumerate(heads):
        parts.append(_box(left + order * cell + 6, top - 18, cell - 12, 30, head, False, PURPLE))
        parts.append(_box(left - 66, top + order * cell + 6, 52, cell - 12, head, False, PURPLE))
    for r, row in enumerate(heads):
        for c, col in enumerate(heads):
            both = (row == "D") + (col == "D")
            fill = "#e7f3ec" if both == 2 else ("#fdf3e3" if both == 1 else "#fdeaea")
            edge = "#2f9e6b" if both == 2 else (ORANGE if both == 1 else RED)
            parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="%s" stroke="%s"/>'
                         % (left + c * cell + 6, top + r * cell + 6, cell - 12, cell - 12,
                            fill, edge))
            parts.append(_text(left + c * cell + cell / 2, top + r * cell + cell / 2 + 5,
                               row + col, 14, "#2b3a46", "middle", True))
    note = ("F2 基因型 DD : Dd : dd = 1 : 2 : 1；"
            "表现型 高茎 : 矮茎 = 3 : 1（绿=纯合显性、橙=杂合、红=隐性）")
    for order, row in enumerate(_wrap(note, per=42, lines=2)):
        parts.append(_text(W / 2, 300 + order * 17, row, 12, "#2b3a46"))
    # 动画：雌雄配子（小球）分别从行/列飞进格子
    for order, head in enumerate(heads):
        parts.append(_dot(_path_of([(left + order * cell + cell / 2, top - 4),
                                    (left + order * cell + cell / 2, top + 4 * cell - 20)]),
                          dur=2.2, begin=0.0, r=6, color=PURPLE))
        parts.append(_dot(_path_of([(left - 40, top + order * cell + cell / 2),
                                    (left + 4 * cell - 20, top + order * cell + cell / 2)]),
                          dur=2.2, begin=round(0.3 + order * 0.2, 2), r=6, color=GREEN))
    return _head("棋盘格：雌雄配子随机结合（小球是配子）") + "".join(parts)


def _di_grid(steps, index):
    parts = []
    heads = ["YR", "Yr", "yR", "yr"]
    cell, left, top = 58, 168, 84
    parts.append(_text(left + cell * 2, top - 28, "雌配子", 12, "#5b6773"))
    for order, head in enumerate(heads):
        parts.append(_box(left + order * cell + 3, top - 22, cell - 6, 26, head, False, PURPLE))
        parts.append(_box(left - 66, top + order * cell + 3, 58, cell - 6, head, False, PURPLE))
    tones = {2: ("#e7f3ec", "#2f9e6b"), 1: ("#fdf3e3", ORANGE), 0: ("#fdeaea", RED)}
    counts = {2: 0, 1: 0, 0: 0}
    for r, row in enumerate(heads):
        for c, col in enumerate(heads):
            # 显性 = 一对里只要有一个显性基因（字母大写）就表现出来
            big = (1 if (row[0] == "Y" or col[0] == "Y") else 0) + \
                  (1 if (row[1] == "R" or col[1] == "R") else 0)
            counts[big] += 1
            fill, edge = tones[big]
            parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="%s" stroke="%s"/>'
                         % (left + c * cell + 3, top + r * cell + 3, cell - 6, cell - 6,
                            fill, edge))
            parts.append(_text(left + c * cell + cell / 2, top + r * cell + cell / 2 + 4,
                               row + col, 11, "#2b3a46"))
    note = ("绿=黄圆 %d 个、橙=黄皱或绿圆 %d 个、红=绿皱 %d 个 -> F2 表现型比 9 : 3 : 3 : 1"
            % (counts[2], counts[1], counts[0]))
    for order, row in enumerate(_wrap(note, per=40, lines=2)):
        parts.append(_text(W / 2, 324 + order * 17, row, 12, "#2b3a46", "middle"))
    # 动画：4 种雌配子从上往下扫、4 种雄配子从左往右扫，扫到哪格就是哪次结合
    for order in range(4):
        parts.append(_dot(_path_of([(left + order * cell + cell / 2, top - 8),
                                    (left + order * cell + cell / 2, top + 4 * cell - 8)]),
                          dur=3.0, begin=0.0, r=6, color=PURPLE))
        parts.append(_dot(_path_of([(left - 44, top + order * cell + cell / 2),
                                    (left + 4 * cell - 8, top + order * cell + cell / 2)]),
                          dur=3.0, begin=round(0.4 + order * 0.25, 2), r=6, color=GREEN))
    return _head("棋盘格：F1（YyRr）自交，4 x 4 = 16 种组合（小球是配子）") + "".join(parts)


def _tree(steps, index):
    count = len(steps)
    parts = []
    root_w, root_h = 150, 46
    parts.append(_box(W / 2 - root_w / 2, 48, root_w, root_h, "同一群细胞（遗传物质相同）",
                      index == 0, PURPLE))
    leaf_w, leaf_h = 178, 58
    span = count - 1
    for order in range(1, count):
        x = (W - leaf_w) / 2 + (order - (span + 1) / 2.0) * (leaf_w + 26)
        y = 200
        parts.append(_arrow(W / 2, 48 + root_h + 3, x + leaf_w / 2, y - 4,
                            order == index, PURPLE))
        parts.append(_box(x, y, leaf_w, leaf_h, steps[order], order == index, GREEN))
    parts.append(_text(W / 2, 320, "基因选择性表达：遗传物质不变，表达出来的蛋白质不同", 12,
                       "#5b6773"))
    # 动画：小球从根一路走到每一片叶子上
    for order in range(1, count):
        x = (W - leaf_w) / 2 + (order - (span + 1) / 2.0) * (leaf_w + 26)
        parts.append(_dot(_path_of([(W / 2, 94), (x + leaf_w / 2, 200)]), dur=2.6,
                          begin=round((order - 1) * 0.9, 2), r=7,
                          color=GREEN if order == index else "#8bb7a2"))
    return _head("分支：同一来源，走向不同的命运（小球分流）") + "".join(parts)


# ---------------- 曲线数据（按公式算，不写死坐标） ----------------

def _steps(low, high, count):
    return [low + (high - low) * i / float(count - 1) for i in range(count)]


def _triples(pairs):
    return [(round(x, 3), round(y, 3), i) for i, (x, y) in enumerate(pairs)]


def _curves(name):
    """返回 (x_label, y_label, extra, note)；extra 是 [(点, 颜色, 名称), …]。"""
    import math
    if name == "barrier":
        xs = _steps(0, 100, 41)
        return ("反应进程", "能量", [
            (_triples([(x, 8 + 62 * math.sin(math.pi * x / 100)) for x in xs]), RED, "无酶：活化能高"),
            (_triples([(x, 8 + 26 * math.sin(math.pi * x / 100)) for x in xs]), GREEN, "有酶：活化能低"),
        ], "酶只降低活化能，不改变反应前后能量差")
    if name == "peak":
        xs = _steps(0, 100, 41)
        return ("温度 / pH（相对值）", "酶活性（相对值）", [
            (_triples([(x, 100 * math.exp(-((x - 50) ** 2) / 400.0)) for x in xs]), BLUE,
             "最适温度/pH 处活性最高"),
        ], "过酸、过碱、高温 -> 变性失活；低温只是抑制")
    if name == "mitosis":
        return ("细胞周期（相对时间）", "数目（2N = 2）", [
            (_triples([(0, 2), (12, 2), (20, 4), (70, 4), (78, 2), (100, 2)]), BLUE, "DNA 数目"),
            (_triples([(0, 2), (48, 2), (54, 4), (70, 4), (76, 2), (100, 2)]), ORANGE,
             "染色体数目"),
        ], "间期 DNA 加倍、染色体不加倍；后期着丝点分裂，染色体数暂时加倍")
    if name == "meiosis":
        return ("减数分裂（相对时间）", "数目（N = 1）", [
            (_triples([(0, 2), (12, 2), (22, 4), (46, 4), (52, 2), (76, 2), (82, 1), (100, 1)]),
             BLUE, "DNA 数目"),
            (_triples([(0, 2), (46, 2), (52, 1), (70, 1), (76, 2), (82, 1), (100, 1)]),
             ORANGE, "染色体数目"),
        ], "减 I 同源染色体分开 -> 数目减半；减 II 后期着丝点分裂，暂时加倍")
    if name == "potential":
        return ("时间 / ms", "膜电位 / mV", [
            (_triples([(0, -70), (1, -70), (1.4, -55), (1.8, 30), (2.4, 30), (3.0, -20),
                       (3.8, -75), (4.4, -80), (5.0, -70), (10, -70)]), BLUE,
             "去极化 -> 复极化 -> 超极化"),
        ], "静息电位外正内负；动作电位外负内正（全或无，幅度不变）")
    if name == "auxin":
        xs = _steps(-12, -2, 41)
        return ("生长素浓度 log(mol/L)", "促进（+）/ 抑制（-）", [
            (_triples([(x, 100 * math.exp(-((x + 8) ** 2) / 8.0) - 12) for x in xs]), GREEN,
             "两重性：低促高抑"),
        ], "顶点 = 最适浓度；再高就抑制（曲线落到 0 以下）")
    if name == "growth":
        return ("时间", "种群数量（相对值）", [
            (_triples([(t * 0.5, 3 * 1.18 ** (t * 0.5)) for t in range(0, 29)]), RED,
             "J 形：Nt = N0 x 指数增长"),
            (_triples([(t * 0.5, 32 / (1 + math.exp(-(t * 0.5 - 7) * 0.9))) for t in range(0, 29)]),
             BLUE, "S 形：受 K 值限制"),
        ], "K/2 时增长速率最大（渔业捕捞后要留下 K/2）")
    if name == "relation":
        xs = _steps(0, 20, 41)
        return ("时间", "数量", [
            (_triples([(x, 50 + 12 * math.sin(x * 0.8)) for x in xs]), BLUE, "被捕食者"),
            (_triples([(x, 26 + 11 * math.sin(x * 0.8 - 1.3)) for x in xs]), RED,
             "捕食者（滞后）"),
        ], "捕食者数量变化总是落后于被捕食者（先增后减）")
    if name == "selection":
        xs = _steps(0, 30, 61)
        return ("世代", "某基因的频率", [
            (_triples([(x, 0.1 + 0.85 / (1 + math.exp(-(x - 15) * 0.5))) for x in xs]), RED,
             "定向选择：一直朝一个方向"),
            (_triples([(x, 0.5 + 0.02 * math.sin(x)) for x in xs]), BLUE, "稳定选择：基本不变"),
            (_triples([(x, 0.5 + 0.02 * math.cos(x)) for x in xs]), GREEN, "分裂选择：基本不变"),
        ], "进化的实质就是种群基因频率发生改变")
    raise ValueError("没有这种曲线：%s" % name)


def _curve_figure(key, steps, index, name):
    """曲线类：主曲线给红点游标，游标跟着阶段走。"""
    import chart as chart_mod
    x_label, y_label, extra, note = _curves(name)
    main = extra[0][0]
    cursor = min(len(main) - 1, int(round(index * (len(main) - 1) / max(1, len(steps) - 1))))
    body = chart_mod.chart(main, x_label, y_label, cursor=cursor, note=note,
                           width=W, height=H - 76, extra=extra[1:], main_label=extra[0][2])
    inner = body.split(">", 1)[1].rsplit("</svg>", 1)[0]
    # 动画：一颗球沿着曲线跑（和坐标轴同一套映射，所以一定贴着线）
    box = chart_mod.meta_of(main, W, H - 76, extra=[group[0] for group in extra[1:]])

    def at(point):
        return (box["left"] + (float(point[0]) - box["xmin"]) / (box["xmax"] - box["xmin"])
                * box["w"],
                box["top"] + box["h"] - (float(point[1]) - box["ymin"])
                / (box["ymax"] - box["ymin"]) * box["h"])

    inner += _dot(_path_of([at(point) for point in main]), dur=4.5, r=7, color=ORANGE)
    return (_head("曲线：横轴是自变量，纵轴随它变（小球沿曲线跑）")
            + '<g transform="translate(0,34)">' + inner + "</g>"
            + _caption(steps, index, H - 32) + "</svg>")


# ---------------- 细胞分裂（细胞级动画：染色体分离 + 细胞一分为二） ----------------

def _move(values, dur, keytimes="0;0.3;0.5;0.75;1", begin=0.0):
    return ('<animateTransform attributeName="transform" type="translate" dur="%gs" '
            'begin="%gs" repeatCount="indefinite" values="%s" keyTimes="%s"/>'
            % (dur, begin, values, keytimes))


def _fade(values, dur, keytimes):
    return ('<animate attributeName="opacity" values="%s" keyTimes="%s" dur="%gs" '
            'repeatCount="indefinite"/>' % (values, keytimes, dur))


def _rod(color):
    """一条染色单体（中间略弯的粗线）。"""
    return ('<path d="M 0 -15 Q 7 0 0 15" stroke="%s" stroke-width="9" fill="none" '
            'stroke-linecap="round"/>' % color)


def _cell(cx, cy, rx, ry, values, keytimes, dur):
    return ('<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="#f7fbff" stroke="#7fa8cf" '
            'stroke-width="2">%s</ellipse>'
            % (cx, cy, rx, ry, _fade(values, dur, keytimes)))


def _cell_label(x, y, text):
    return _text(x, y, text, 12, "#5b6773")


def _division_scene(steps, index, meiosis=False):
    """有丝分裂 / 减数分裂：染色体在动、细胞在裂。

    一条时间轴循环演完（有丝 11 秒、减数 15 秒），阶段文字照旧在下面；
    画面按段演：只有 index 这一段亮着并脉动（本段的染色体/细胞轮廓高亮），别的段淡出。
    """
    dur = 15.0 if meiosis else 11.0
    cx, cy = 330.0, 178.0
    n = 4 if meiosis else 5
    seg = [[] for _ in range(n)]
    active = min(n - 1, index * (n - 1) // max(1, len(steps) - 1))
    parts = ['<ellipse cx="%g" cy="%g" rx="152" ry="118" fill="#eef6fd" stroke="#c3d8ec" '
             'stroke-width="26" opacity="0.55"/>' % (cx, cy)]
    # 公共背景：细胞轮廓（一个细胞 -> 两个 -> 四个，切换时刻和染色体分离的时刻对齐）
    if meiosis:
        parts.append(_cell(cx, cy, 150, 116, "1;1;0;0", "0;0.42;0.48;1", dur))
        parts.append(_cell(cx - 80, cy, 74, 116, "0;0;1;1;0;0", "0;0.42;0.48;0.78;0.84;1", dur))
        parts.append(_cell(cx + 80, cy, 74, 116, "0;0;1;1;0;0", "0;0.42;0.48;0.78;0.84;1", dur))
        for dx in (-80, 80):
            for dy in (-58, 58):
                parts.append(_cell(cx + dx, cy + dy, 72, 54, "0;0;0;1", "0;0.42;0.78;0.84", dur))
    else:
        parts.append(_cell(cx, cy, 150, 116, "1;1;0;0", "0;0.5;0.56;1", dur))
        parts.append(_cell(cx - 80, cy, 74, 116, "0;0;1;1", "0;0.5;0.56;1", dur))
        parts.append(_cell(cx + 80, cy, 74, 116, "0;0;1;1", "0;0.5;0.56;1", dur))
    # 公共背景：核膜（间期）与纺锤丝
    parts.append('<circle cx="%g" cy="%g" r="62" fill="none" stroke="#b8c8d8" '
                 'stroke-width="2" stroke-dasharray="5 5">%s</circle>'
                 % (cx, cy, _fade("1;0.9;0;0", dur, "0;0.12;0.22;1")))
    if meiosis:
        parts.append('<g stroke="#c9b6e4" stroke-width="1.6">%s%s</g>'
                     % ('<line x1="180" y1="%g" x2="%g" y2="%g">%s</line>'
                        % (cy, cx, cy, _fade("0;0;1;0", dur, "0;0.15;0.3;0.5")),
                        '<line x1="480" y1="%g" x2="%g" y2="%g">%s</line>'
                        % (cy, cx, cy, _fade("0;0;1;0", dur, "0;0.15;0.3;0.5"))))
    else:
        for sign in (-1, 1):
            parts.append('<g stroke="#c9b6e4" stroke-width="1.6">'
                         '<line x1="%g" y1="%g" x2="%g" y2="%g">%s</line>'
                         '<line x1="%g" y1="%g" x2="%g" y2="%g">%s</line></g>'
                         % (cx + sign * 150, cy, cx, cy - 60,
                            _fade("0;0;1;0", dur, "0;0.15;0.3;0.55"),
                            cx + sign * 150, cy, cx, cy + 60,
                            _fade("0;0;1;0", dur, "0;0.15;0.3;0.55")))
    # 染色体：每条 = 两条姐妹染色单体（每段各拿一份，只有本段那份是亮的）
    rods = []
    if meiosis:
        # 减 I：同源染色体联会后分开（红去左细胞、蓝去右细胞）
        # 减 II：每个细胞里姐妹染色单体再分开（上下各得一条）
        keytimes = "0;0.15;0.32;0.5;0.72;1"
        for order, color in enumerate((RED, BLUE)):
            party = -1 if order == 0 else 1
            for side in (-1, 1):
                inner = "%g,%g; %g,%g; %g,%g; %g,%g; %g,%g; %g,%g" % (
                    -46 + order * 92 + side * 9, -66 + order * 34,
                    party * 74 + side * 8, 0,
                    party * 74 + side * 8, 0,
                    party * 80 + side * 10, side * 14,
                    party * 80 + side * 10, side * 58,
                    party * 80 + side * 10, side * 58)
                rods.append('<g transform="translate(%g,%g)"><g>%s%s</g></g>'
                            % (cx, cy, _move(inner, dur, keytimes), _rod(color)))
    else:
        keytimes = "0;0.28;0.45;0.68;1"
        starts = ((-72, -52), (58, 46))
        for order in range(2):
            slot = -46 + order * 92
            for side in (-1, 1):
                inner = "%g,%g; %g,%g; %g,%g; %g,%g; %g,%g" % (
                    starts[order][0] + side * 9, starts[order][1],
                    slot + side * 8, 0,
                    slot + side * 8, 0,
                    side * 112, 0,
                    side * 112, 0)
                rods.append('<g transform="translate(%g,%g)"><g>%s%s</g></g>'
                            % (cx, cy, _move(inner, dur, keytimes),
                               _rod(RED if order == 0 else BLUE)))
    rods = "".join(rods)
    # 每段专属的分相标记：跟着本段一起亮、一起脉动
    if meiosis:
        # 第 1 段（间期）：DNA 复制，染色体数不变
        seg[0].append('<g stroke="#8a5fb0" stroke-width="2.6" fill="none">'
                      '<path d="M 296 166 q 16 -14 32 2 q 16 16 32 0"/>'
                      '<path d="M 296 192 q 16 14 32 -2 q 16 -16 32 0"/></g>')
        seg[0].append('<circle cx="%g" cy="%g" r="68" fill="none" stroke="#8a5fb0" '
                      'stroke-width="2.5" stroke-dasharray="8 6"/>' % (cx, cy))
        seg[0].append(_tag(cx, 96, "间期：DNA 复制，染色体数不变", "#5b4f77"))
        # 第 2 段（减 I 前期）：同源染色体联会成四分体
        seg[1].append(rods)
        for color, x0 in ((RED, -52), (BLUE, 8)):
            for dx in (0, 22):
                seg[1].append('<g transform="translate(%g,%g)">'
                              '<g transform="scale(1.5)">%s</g></g>'
                              % (cx + x0 + dx, cy, _rod(color)))
        seg[1].append('<circle cx="%g" cy="%g" r="68" fill="none" stroke="#7a4fb0" '
                      'stroke-width="2.5" stroke-dasharray="8 6"/>' % (cx, cy))
        seg[1].append(_tag(cx, 96, "减 I 前期：同源染色体联会成四分体", "#7a4fb0"))
        # 第 3 段（减 I 后期）：同源染色体分离，移向两极
        seg[2].append(rods)
        seg[2].append(_arrow(cx - 24, cy + 88, cx - 132, cy + 88, True, "#c0392b"))
        seg[2].append(_arrow(cx + 24, cy + 88, cx + 132, cy + 88, True, "#2f6fd0"))
        seg[2].append(_tag(cx, 96, "减 I 后期：同源染色体分离，移向两极", "#a33f3f"))
        # 第 4 段（减 II 与末期）：姐妹染色单体分开，形成 4 个子细胞
        seg[3].append(rods)
        for dx in (-80, 80):
            for dy in (-58, 58):
                seg[3].append('<ellipse cx="%g" cy="%g" rx="72" ry="54" fill="none" '
                              'stroke="#2f7d52" stroke-width="3" stroke-dasharray="9 7"/>'
                              % (cx + dx, cy + dy))
        seg[3].append(_tag(cx, 96, "减 II：形成 4 个子细胞，染色体数减半", "#2f7d52"))
    else:
        # 第 1 段（间期）：核膜核仁清晰，染色质细长，DNA 在复制
        seg[0].append('<g stroke="#8a5fb0" stroke-width="2.6" fill="none">'
                      '<path d="M 296 166 q 16 -14 32 2 q 16 16 32 0"/>'
                      '<path d="M 296 192 q 16 14 32 -2 q 16 -16 32 0"/></g>')
        seg[0].append('<circle cx="%g" cy="%g" r="68" fill="none" stroke="#8a5fb0" '
                      'stroke-width="2.5" stroke-dasharray="8 6"/>' % (cx, cy))
        seg[0].append(_tag(cx, 96, "间期：核膜核仁清晰，DNA 复制", "#5b4f77"))
        # 第 2 段（前期）：核膜消失、染色质变成染色体、纺锤体形成
        seg[1].append(rods)
        seg[1].append('<circle cx="%g" cy="%g" r="96" fill="none" stroke="#c0392b" '
                      'stroke-width="2.5" stroke-dasharray="9 7"/>' % (cx, cy))
        seg[1].append(_tag(cx, 96, "前期：染色体出现，纺锤体形成", "#a33f3f"))
        # 第 3 段（中期）：着丝点排在赤道板上
        seg[2].append(rods)
        seg[2].append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#e08a2b" '
                      'stroke-width="2.5" stroke-dasharray="9 7"/>'
                      % (cx, cy - 112, cx, cy + 112))
        seg[2].append(_text(cx + 58, cy - 116, "赤道板", 12, "#a3721a"))
        seg[2].append(_tag(cx, 96, "中期：着丝点排在赤道板上", "#a3721a"))
        # 第 4 段（后期）：着丝点分裂，姐妹染色单体分开，移向两极
        seg[3].append(rods)
        seg[3].append(_arrow(cx + 24, cy + 88, cx + 132, cy + 88, True, "#2f6fd0"))
        seg[3].append(_arrow(cx - 24, cy + 88, cx - 132, cy + 88, True, "#c0392b"))
        seg[3].append(_tag(cx, 96, "后期：着丝点分裂，移向两极", "#2f6fd0"))
        # 第 5 段（末期）：核膜重建、细胞质分裂成两个子细胞
        seg[4].append(rods)
        seg[4].append('<path d="M %g %g q -16 116 0 232" fill="none" stroke="#2f9e6b" '
                      'stroke-width="3" stroke-dasharray="9 7"/>' % (cx, cy - 116))
        for sign in (-1, 1):
            seg[4].append('<ellipse cx="%g" cy="%g" rx="74" ry="116" fill="none" '
                          'stroke="#2f9e6b" stroke-width="3" stroke-dasharray="9 7"/>'
                          % (cx + sign * 80, cy))
        seg[4].append(_tag(cx, 96, "末期：细胞质分裂成两个子细胞", "#2f7d52"))
    # 只有本段亮着并脉动，别的段淡出
    for order, body in enumerate(seg):
        on = order == active
        parts.append('<g opacity="%g">%s%s</g>'
                     % (1 if on else 0.24, _pulse(1.8, 0.6) if on else "", "".join(body)))
    return (_head("细胞分裂：染色体分离、细胞一分为%s（循环播放）" % ("四" if meiosis else "二"))
            + "".join(parts) + _caption(steps, index, H - 30) + "</svg>")



# ==================== 专门的仿真场景（每个过程一套，走位/粒子都是自己编的） ====================

def _sim_photosynthesis(steps, index):
    """光合作用：太阳发光 -> 光子打到类囊体薄膜 -> 水被分解冒氧气 -> 产生的 ATP/NADPH
    送到基质里去固定 CO2 -> 合成糖运走。所有粒子一直在动，循环演。"""
    parts = ['<rect width="%d" height="%d" fill="#f2f8ff"/>' % (W, H)]
    # 太阳 + 光线（一直在转 / 一亮一暗）
    parts.append('<g transform="translate(74,66)">'
                 '<circle r="26" fill="#ffd75e" stroke="#f0a92b" stroke-width="2">%s</circle>'
                 '<g stroke="#f0a92b" stroke-width="3" stroke-linecap="round">%s</g></g>'
                 % (_pulse(2.4, 0.55), "".join(
                     '<line x1="%g" y1="0" x2="%g" y2="0" transform="rotate(%d)"/>'
                     % (32, 44, angle) for angle in range(0, 360, 45))))
    parts.append(_text(74, 118, "光能", 12, "#a3721a"))
    # 叶绿体：外膜 + 基质 + 基粒（类囊体堆）
    parts.append('<ellipse cx="360" cy="212" rx="248" ry="126" fill="#e8f6ea" '
                 'stroke="#79b98a" stroke-width="3"/>')
    parts.append(_text(360, 108, "叶绿体", 13, "#3f7a51"))
    for stack, sx in enumerate((250, 340, 430)):
        for disc in range(4):
            parts.append('<ellipse cx="%g" cy="%g" rx="34" ry="9" fill="#9fd3a8" '
                         'stroke="#5aa06d" stroke-width="1.6">%s</ellipse>'
                         % (sx, 158 + disc * 15, _pulse(1.8, 0.6, stack * 0.4)))
    parts.append(_text(340, 232, "类囊体薄膜（光反应）", 11.5, "#3f7a51"))
    # 基质里的暗反应区（卡尔文循环）
    parts.append('<circle cx="520" cy="272" r="58" fill="#fdf6e3" stroke="#e0c274" '
                 'stroke-width="2" stroke-dasharray="6 5"/>')
    parts.append(_text(520, 330, "基质（暗反应 / 卡尔文循环）", 11.5, "#8a6a1f"))
    # 光子：从太阳射向基粒
    for order, (ty, dur) in enumerate(((166, 1.4), (186, 1.7), (206, 1.5))):
        parts.append(_dot(_path_of([(104, 78), (232, ty)]), dur=dur,
                          begin=round(order * 0.45, 2), r=6, color="#ffcf4d"))
    # 水进来 -> 在薄膜上分解 -> 氧气冒出去
    for order, sx in enumerate((212, 252)):
        parts.append(_dot(_path_of([(150, 340), (sx, 200)]), dur=2.6,
                          begin=round(order * 0.9, 2), r=6, color="#4aa3e0"))
        parts.append(_dot(_path_of([(sx, 176), (330 + order * 40, 52)]), dur=2.8,
                          begin=round(1.2 + order * 0.8, 2), r=6, color="#7fd4f5"))
    parts.append(_text(150, 360, "H2O", 12, "#1f6ea8"))
    parts.append(_text(388, 48, "O2", 12, "#1f8ab8"))
    # ATP / NADPH：薄膜 -> 基质
    for order in range(3):
        parts.append(_dot(_path_of([(452, 176), (500, 240)]), dur=1.6,
                          begin=round(order * 0.5, 2), r=6, color="#e08a2b"))
        parts.append(_dot(_path_of([(470, 196), (508, 252)]), dur=1.9,
                          begin=round(0.3 + order * 0.5, 2), r=5, color="#8a6cd0"))
    parts.append(_text(474, 232, "ATP / NADPH", 11.5, "#a35a12"))
    # CO2 进基质 -> 糖出去
    for order in range(2):
        parts.append(_dot(_path_of([(660, 300 - order * 40), (566, 282 - order * 30)]),
                          dur=2.2, begin=round(order * 0.7, 2), r=6, color="#8d949c"))
        parts.append(_dot(_path_of([(556, 300), (640, 366)]), dur=2.6,
                          begin=round(1.0 + order * 0.9, 2), r=6, color="#3f9e6b"))
    parts.append(_text(636, 272, "CO2", 12, "#5b6773"))
    parts.append(_text(600, 372, "糖", 12, "#2f7d52"))
    return (_head("光合作用（仿真：光子、水、CO2 一直在流动）") + "".join(parts)
            + _caption(steps, index, H - 34) + "</svg>")


def _sim_respiration(steps, index):
    """有氧呼吸：细胞质基质里葡萄糖分解成丙酮酸 -> 丙酮酸进线粒体基质放出 CO2 和 [H]
    -> [H] 在内膜上和 O2 结合成水并放出大量 ATP。粒子一直在跑。"""
    parts = ['<rect width="%d" height="%d" fill="#f6f4ef"/>' % (W, H)]
    parts.append('<rect x="14" y="86" width="176" height="212" rx="14" fill="#fff8e7" '
                 'stroke="#e0c274" stroke-width="2"/>')
    parts.append(_text(102, 76, "细胞质基质", 12, "#8a6a1f"))
    # 葡萄糖（六边形）
    hexagon = " ".join("%g,%g" % (70 + 22 * __import__("math").cos(a * 3.14159 / 180),
                                  150 + 22 * __import__("math").sin(a * 3.14159 / 180))
                       for a in range(0, 360, 60))
    parts.append('<polygon points="%s" fill="#f0b429" stroke="#c98a12" stroke-width="2">%s'
                 '</polygon>' % (hexagon, _pulse(1.8, 0.55)))
    parts.append(_text(70, 186, "葡萄糖", 12, "#8a6a1f"))
    parts.append(_text(160, 120, "第一阶段", 11, "#a3721a"))
    # 线粒体（外膜 + 内膜褶皱）
    parts.append('<ellipse cx="428" cy="196" rx="196" ry="112" fill="#fdecec" '
                 'stroke="#c96a6a" stroke-width="3"/>')
    for order in range(5):
        x = 300 + order * 62
        parts.append('<path d="M %g 116 q 26 44 0 78 q -26 34 0 78" fill="none" '
                     'stroke="#e08a8a" stroke-width="6" stroke-linecap="round">%s</path>'
                     % (x, _pulse(2.2, 0.6, order * 0.3)))
    parts.append(_text(428, 74, "线粒体", 13, "#a33f3f"))
    parts.append(_text(300, 258, "基质（第二阶段）", 11.5, "#a33f3f"))
    parts.append(_text(520, 168, "内膜（第三阶段）", 11.5, "#a33f3f"))
    # 丙酮酸：从细胞质进基质
    for order in range(2):
        parts.append(_dot(_path_of([(150, 214 + order * 22), (268, 214 + order * 22)]),
                          dur=2.4, begin=round(order * 0.9, 2), r=7, color="#f0b429"))
    parts.append(_text(206, 272, "丙酮酸", 11.5, "#8a6a1f"))
    # 第二阶段：放 CO2 + 产生 [H]
    for order in range(3):
        parts.append(_dot(_path_of([(330, 200), (596, 74)]), dur=2.8,
                          begin=round(order * 0.8, 2), r=6, color="#9aa4ad"))
        parts.append(_dot(_path_of([(322, 224), (452, 288)]), dur=2.2,
                          begin=round(0.4 + order * 0.6, 2), r=5, color=RED))
    parts.append(_text(614, 62, "CO2", 12, "#5b6773"))
    parts.append(_text(408, 306, "[H]", 12, "#a33f3f"))
    # 第三阶段：O2 进来，水出去，ATP 大量放出
    for order in range(2):
        parts.append(_dot(_path_of([(640, 322), (486, 268)]), dur=2.6,
                          begin=round(order * 1.1, 2), r=6, color="#4aa3e0"))
        parts.append(_dot(_path_of([(470, 250), (250, 322)]), dur=2.9,
                          begin=round(0.9 + order * 0.9, 2), r=6, color="#7fd4f5"))
    for order in range(4):
        parts.append(_dot(_path_of([(508, 210), (656, 356)]), dur=1.9,
                          begin=round(order * 0.45, 2), r=6, color=ORANGE))
    parts.append(_text(596, 316, "O2", 12, "#1f6ea8"))
    parts.append(_text(196, 326, "H2O", 12, "#1f8ab8"))
    parts.append(_text(600, 332, "大量 ATP", 12, "#a35a12"))
    return (_head("有氧呼吸（仿真：葡萄糖 -> 丙酮酸 -> CO2+[H] -> 水 + 大量 ATP）")
            + "".join(parts) + _caption(steps, index, H - 26) + "</svg>")


def _tag(x, y, label, color="#3a4652"):
    """带白底的标签：压在线或粒子上也看得清。"""
    width = len(label) * 7.4 + 16
    return ('<g><rect x="%g" y="%g" width="%g" height="21" rx="6" fill="#ffffff" '
            'opacity="0.94" stroke="%s" stroke-width="0.8"/>%s</g>'
            % (x - width / 2, y - 15.5, width, color, _text(x, y, label, 12, color)))


def _sim_replication(steps, index):
    """DNA 复制：**长链在往左走、复制叉固定在画面中间**（像真的复制叉沿 DNA 前进），
    左侧还是配对的双链，右侧已经变成两条子代 DNA（各一条母链 + 一条新链）。
    游离的脱氧核苷酸就在叉口附近落进去配对。"""
    parts = ['<rect width="%d" height="%d" fill="#f5f7fb"/>' % (W, H)]
    parts.append(_text(132, 54, "母链（碱基配对）", 12, "#5b6773"))
    parts.append(_tag(330, 54, "复制叉", "#a3721a"))
    parts.append(_text(528, 54, "两条子代 DNA", 12, "#2f7d52"))
    parts.append('<defs>'
                 '<clipPath id="rpl"><rect x="0" y="72" width="300" height="196"/></clipPath>'
                 '<clipPath id="rpr"><rect x="300" y="72" width="360" height="196"/>'
                 '</clipPath></defs>')
    # 长链一直往左走；横档每 20 px 一根，所以"走一格再跳回来"看不出来
    scroll = ('<animateTransform attributeName="transform" type="translate" '
              'values="0,0; -20,0" dur="0.9s" repeatCount="indefinite"/>')
    left = ['<line x1="-300" y1="176" x2="340" y2="176" stroke="#3f6ea8" stroke-width="7"/>',
            '<line x1="-300" y1="208" x2="340" y2="208" stroke="#8a5fb0" stroke-width="7"/>']
    for spot in range(-280, 340, 20):
        left.append('<line x1="%d" y1="176" x2="%d" y2="208" stroke="#c3cede" '
                    'stroke-width="3"/>' % (spot, spot))
    parts.append('<g clip-path="url(#rpl)"><g>%s%s</g></g>' % (scroll, "".join(left)))
    right = []
    for offset, parent, new_color in ((0, "#3f6ea8", "#2f9e6b"), (1, "#8a5fb0", "#2f9e6b")):
        top = 150 if offset == 0 else 234
        new_y = top + 22 if offset == 0 else top - 22
        right.append('<line x1="280" y1="%d" x2="900" y2="%d" stroke="%s" stroke-width="7"/>'
                     % (top, top, parent))
        right.append('<line x1="280" y1="%d" x2="900" y2="%d" stroke="%s" stroke-width="7"/>'
                     % (new_y, new_y, new_color))
        for spot in range(280, 900, 20):
            right.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#9fd3a8" '
                         'stroke-width="3.2"/>' % (spot, min(top, new_y) + 5, spot,
                                                   max(top, new_y) - 5))
    parts.append('<g clip-path="url(#rpr)"><g>%s%s</g></g>' % (scroll, "".join(right)))
    # 叉口（固定）：四条短线把左右两边接起来 + 解旋酶
    parts.append('<path d="M 286 176 q 16 -14 30 -26" fill="none" stroke="#3f6ea8" '
                 'stroke-width="7"/>')
    parts.append('<path d="M 286 208 q 16 14 30 26" fill="none" stroke="#8a5fb0" '
                 'stroke-width="7"/>')
    parts.append('<path d="M 286 192 q 18 -8 32 -20" fill="none" stroke="#2f9e6b" '
                 'stroke-width="7"/>')
    parts.append('<path d="M 286 192 q 18 8 32 20" fill="none" stroke="#2f9e6b" '
                 'stroke-width="7"/>')
    parts.append('<path d="M 298 176 L 322 192 L 298 208 Z" fill="#f0a92b" '
                 'stroke="#c98a12" stroke-width="2">%s</path>' % _pulse(1.6, 0.55))
    parts.append(_tag(330, 284, "解旋酶（把双链解开）", "#a3721a"))
    # 游离的脱氧核苷酸：从叉口上方/下方就近落进去（落地即淡出，不会"啪"地消失）
    colors = ["#d94f4f", "#3f9e6b", "#3f6ea8", "#8a5fb0"]
    for order in range(4):
        parts.append(_dot(_path_of([(256 - order * 14, 96), (294 - order * 4, 160)]),
                          dur=1.8, begin=round(order * 0.45, 2), r=6.5,
                          color=colors[order]))
        parts.append(_dot(_path_of([(256 - order * 14, 288), (294 - order * 4, 224)]),
                          dur=1.8, begin=round(0.9 + order * 0.45, 2), r=6.5,
                          color=colors[(order + 2) % 4]))
    for order in range(4):
        parts.append('<circle cx="%d" cy="%d" r="6.5" fill="%s">%s</circle>'
                     % (74 + order * 24, 118, colors[order], _pulse(1.8, 0.6, order * 0.3)))
    parts.append(_tag(118, 94, "游离的脱氧核苷酸", "#5b6773"))
    parts.append(_tag(556, 126, "新链", "#2f7d52"))
    parts.append(_tag(556, 260, "新链", "#2f7d52"))
    parts.append(_text(W / 2, 300, "每个子代 DNA = 一条母链 + 一条新链（半保留复制）", 12,
                       "#3a4652"))
    return (_head("DNA 复制（仿真：长链往左走，复制叉固定在中间，新链跟在叉后面）")
            + "".join(parts) + _caption(steps, index, H - 26) + "</svg>")


def _amino(x, y, r_text, color="#3f6ea8"):
    """一个氨基酸：中心碳 + 氨基 + 羧基 + 氢 + R 基。"""
    parts = ['<circle cx="%g" cy="%g" r="17" fill="#fff" stroke="%s" stroke-width="2.4"/>'
             % (x, y, color), _text(x, y + 4, "C", 13, color)]
    for dx, dy, name in ((-40, -16, "H2N"), (40, -16, "COOH"), (0, 34, "H"),
                         (0, -38, r_text)):
        parts.append('<circle cx="%g" cy="%g" r="15" fill="#eef4fb" stroke="%s" '
                     'stroke-width="1.6"/>' % (x + dx, y + dy, color))
        parts.append(_text(x + dx, y + dy + 4, name, 10.5, "#3a4652"))
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="2"/>'
                 % (x, y, x + dx, y + dy, color))
    return "".join(parts)


def _sim_dehydration(steps, index):
    """脱水缩合：两个氨基酸靠近，羧基脱 —OH、氨基脱 —H，形成一个肽键并脱下一分子水；
    更多氨基酸接上去就成了肽链。"""
    parts = ['<rect width="%d" height="%d" fill="#f6f9fd"/>' % (W, H)]
    parts.append(_text(112, 60, "氨基酸①", 12, "#3f6ea8"))
    parts.append(_text(528, 60, "氨基酸②", 12, "#8a5fb0"))
    # 两个氨基酸靠拢（来回移动 = 靠近、结合）
    parts.append('<g transform="translate(0,0)"><g>%s%s</g></g>'
                 % (_move("-26,0; 8,0; 8,0; -26,0", 6.0, "0;0.3;0.85;1"),
                    _amino(140, 132, "R1")))
    parts.append('<g transform="translate(0,0)"><g>%s%s</g></g>'
                 % (_move("26,0; -8,0; -8,0; 26,0", 6.0, "0;0.3;0.85;1"),
                    _amino(520, 132, "R2", "#8a5fb0")))
    # 脱下来的 —OH 和 —H 合成水飞走
    parts.append(_dot(_path_of([(452, 116), (330, 74), (200, 46)]), dur=6.0, r=7,
                      color="#4aa3e0", label="OH"))
    parts.append(_dot(_path_of([(146, 96), (250, 60), (330, 44)]), dur=6.0, begin=0.15,
                      r=7, color="#7fd4f5", label="H"))
    parts.append(_text(268, 40, "脱下一分子水", 11.5, "#1f6ea8"))
    # 形成的肽键（在中线处闪）
    parts.append('<g transform="translate(330,132)"><circle r="24" fill="none" '
                 'stroke="%s" stroke-width="3" stroke-dasharray="6 5">%s</circle></g>'
                 % (ORANGE, _fade("0;0;1;1;0", 6.0, "0;0.3;0.36;0.82;1")))
    parts.append(_text(330, 168, "肽键 —CO—NH—", 12, "#a35a12"))
    # 下面：一条肽链（4 个氨基酸用肽键连起来）
    parts.append('<line x1="60" y1="270" x2="600" y2="270" stroke="#cdd7e2" '
                 'stroke-width="2" stroke-dasharray="6 6"/>')
    for order in range(4):
        x = 110 + order * 145
        parts.append(_amino(x, 270, "R%d" % (order + 1),
                            "#3f6ea8" if order % 2 == 0 else "#8a5fb0"))
    for order in range(3):
        x = 110 + order * 145
        parts.append('<line x1="%g" y1="270" x2="%g" y2="270" stroke="%s" stroke-width="5"/>'
                     % (x + 40, x + 105, ORANGE))
    parts.append(_dot(_path_of([(110, 270), (545, 270)]), dur=4.0, r=8, color=GREEN))
    parts.append(_text(330, 330, "n 个氨基酸连成一条肽链：脱 n-1 个水、形成 n-1 个肽键", 12,
                       "#3a4652"))
    return (_head("脱水缩合形成肽键（仿真：两个氨基酸接上、脱下 1 分子水）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_protein_structure(steps, index):
    """蛋白质的四级结构：一级（氨基酸顺序）-> 二级（α 螺旋/折叠）-> 三级（整条链折叠）
    -> 四级（多条链聚在一起）。四个区同时在动。"""
    parts = ['<rect width="%d" height="%d" fill="#f7f8fc"/>' % (W, H)]
    titles = [("一级", "氨基酸排列顺序"), ("二级", "α 螺旋 / β 折叠"),
              ("三级", "整条肽链折叠"), ("四级", "多条肽链聚集")]
    for order, (name, note) in enumerate(titles):
        x = 20 + order * 158
        on = order == min(index, 3)
        parts.append('<rect x="%g" y="52" width="146" height="240" rx="12" fill="%s" '
                     'stroke="%s" stroke-width="%g"/>'
                     % (x, "#eaf3ff" if on else "#ffffff", "#3f6ea8" if on else "#dbe3ec",
                        2.4 if on else 1.4))
        parts.append(_text(x + 73, 76, name, 13, "#3f6ea8" if on else "#5b6773"))
        parts.append(_text(x + 73, 284, note, 10.5, "#7b8794"))
    # 一级：珠子串，一颗珠子在跑
    for order in range(7):
        parts.append('<circle cx="%g" cy="140" r="9" fill="%s"/>'
                     % (44 + order * 17, ["#3f6ea8", "#3f9e6b", "#e08a2b", "#8a5fb0"][order % 4]))
    parts.append(_dot(_path_of([(44, 140), (146, 140)]), dur=2.4, r=7, color=RED))
    parts.append(_text(93, 176, "肽键相连", 10.5, "#7b8794"))
    # 二级：螺旋（虚线流动）
    parts.append('<path d="M 196 122 q 26 18 0 36 q -26 18 0 36 q 26 18 0 36" fill="none" '
                 'stroke="#3f9e6b" stroke-width="6" stroke-linecap="round" '
                 'stroke-dasharray="12 8"><animate attributeName="stroke-dashoffset" '
                 'values="40;0" dur="1.6s" repeatCount="indefinite"/></path>')
    parts.append('<path d="M 240 130 L 288 160 L 240 190 L 288 220" fill="none" '
                 'stroke="#8a5fb0" stroke-width="5" stroke-linecap="round" opacity="0.75"/>')
    # 三级：折叠成一团
    parts.append('<path d="M 380 130 q 40 -26 62 6 q 26 34 -8 54 q -40 22 -62 -6 q -22 -30 8 -54 Z" '
                 'fill="#fdecec" stroke="#d98a8a" stroke-width="3">%s</path>'
                 % _pulse(2.2, 0.6))
    parts.append(_text(410, 200, "空间结构", 10.5, "#a33f3f"))
    # 四级：四条链聚过来
    for order in range(4):
        angle = order * 90
        parts.append('<g>%s<circle r="17" fill="%s" stroke="#3f6ea8" stroke-width="2"/></g>'
                     % (_motion(_ellipse_path(548, 172, 46, 56), dur=5.0,
                                begin=round(order * 1.25, 2)),
                        ["#9fd3a8", "#f0b429", "#8ab6e0", "#d98a8a"][order]))
    parts.append(_text(548, 176, "血红蛋白", 9.5, "#3a4652"))
    parts.append(_text(W / 2, 336, "结构决定功能：一级结构由基因决定，折叠错了功能就废了", 11.5,
                       "#3a4652"))
    return (_head("蛋白质的四级结构（仿真：一层层折起来）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_membrane_model(steps, index):
    """流动镶嵌模型：磷脂双分子层做骨架，蛋白质镶嵌/贯穿/覆盖在上面，整体能侧向流动
    （整层一直在左右漂）。"""
    parts = ['<rect width="%d" height="%d" fill="#eef7fd"/>' % (W, H)]
    # 磷脂分子：头 + 两条尾巴；整层一起左右漂 = 流动性
    lipids = []
    for order, x in enumerate(range(28, 636, 26)):
        lipids.append('<circle cx="%d" cy="176" r="10" fill="#f6d488" stroke="#d9ab3c" '
                      'stroke-width="1.6"/>' % x)
        lipids.append('<line x1="%d" y1="186" x2="%d" y2="212" stroke="#d9ab3c" '
                      'stroke-width="3"/>' % (x - 4, x - 4))
        lipids.append('<line x1="%d" y1="186" x2="%d" y2="212" stroke="#d9ab3c" '
                      'stroke-width="3"/>' % (x + 4, x + 4))
        lipids.append('<circle cx="%d" cy="224" r="10" fill="#f6d488" stroke="#d9ab3c" '
                      'stroke-width="1.6"/>' % x)
        lipids.append('<line x1="%d" y1="214" x2="%d" y2="188" stroke="#d9ab3c" '
                      'stroke-width="3"/>' % (x - 4, x - 4))
        lipids.append('<line x1="%d" y1="214" x2="%d" y2="188" stroke="#d9ab3c" '
                      'stroke-width="3"/>' % (x + 4, x + 4))
    parts.append('<g>%s<animateTransform attributeName="transform" type="translate" '
                 'values="-14,0; 14,0; -14,0" dur="6s" repeatCount="indefinite"/></g>'
                 % "".join(lipids))
    # 贯穿蛋白 / 镶嵌蛋白 / 表面蛋白
    parts.append('<path d="M 150 150 q 22 50 0 100 q -22 -50 0 -100 Z" fill="#8ab6e0" '
                 'stroke="#3f6ea8" stroke-width="2">%s</path>' % _pulse(2.0, 0.6))
    parts.append('<path d="M 396 156 q 20 44 0 88 q -20 -44 0 -88 Z" fill="#8ab6e0" '
                 'stroke="#3f6ea8" stroke-width="2"/>')
    parts.append('<ellipse cx="262" cy="160" rx="34" ry="18" fill="#9fd3a8" stroke="#3f9e6b" '
                 'stroke-width="2">%s</ellipse>' % _pulse(2.4, 0.55, 0.6))
    parts.append('<ellipse cx="500" cy="244" rx="34" ry="16" fill="#c8a2e0" stroke="#8a5fb0" '
                 'stroke-width="2"/>')
    parts.append('<ellipse cx="556" cy="150" rx="30" ry="15" fill="#c8a2e0" stroke="#8a5fb0" '
                 'stroke-width="2"/>')
    # 分子过膜（小颗粒穿过去）
    for order in range(3):
        parts.append(_dot(_path_of([(120 + order * 40, 292), (140 + order * 40, 122)]),
                          dur=3.0, begin=round(order * 0.7, 2), r=6, color="#4aa3e0"))
    parts.append(_text(70, 176, "磷脂双分子层", 11.5, "#8a6a1f"))
    parts.append(_text(330, 62, "磷脂：亲水头朝外、疏水尾朝内；蛋白质镶嵌或贯穿", 12,
                       "#3a4652"))
    parts.append(_text(330, 330, "磷脂和大多数蛋白质都能侧向流动 —— 所以细胞膜有流动性", 11.5,
                       "#3a4652"))
    return (_head("细胞膜流动镶嵌模型（仿真：整层在漂、分子在穿）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_plasmolysis(steps, index):
    """质壁分离与复原：外界浓度高时细胞失水、原生质层收缩离开细胞壁；换成清水又吸水复原。
    原生质层是真的在缩、水分子真的在往外出。"""
    parts = ['<rect width="%d" height="%d" fill="#fdf7ef"/>' % (W, H)]
    # 外界溶液（溶质多）
    for order in range(26):
        x = 40 + (order % 13) * 46
        y = 62 + (order // 13) * 268
        parts.append('<circle cx="%d" cy="%d" r="5" fill="#e0b48a">%s</circle>'
                     % (x, y, _pulse(2.6, 0.65, order * 0.1)))
    parts.append(_text(330, 48, "外界溶液（浓度高）", 12, "#a3721a"))
    # 细胞壁
    parts.append('<rect x="96" y="86" width="468" height="228" rx="18" fill="#f4f9ee" '
                 'stroke="#8aa86a" stroke-width="10"/>')
    parts.append(_text(330, 322, "细胞壁（全透性，基本不变形）", 11.5, "#5f7a45"))
    # 原生质层 + 液泡：整体等比缩小 -> 与细胞壁分离
    parts.append('<g transform="translate(330,200)"><g>%s'
                 '<ellipse cx="0" cy="0" rx="216" ry="100" fill="#eaf6ff" '
                 'stroke="#7fa8cf" stroke-width="3"/>'
                 '<ellipse cx="0" cy="0" rx="150" ry="62" fill="#e6ddf6" '
                 'stroke="#8a7fb0" stroke-width="3"/>%s</g></g>'
                 % ('<animateTransform attributeName="transform" type="scale" '
                    'values="1,1; 0.82,0.72; 1,1" dur="7s" repeatCount="indefinite"/>',
                    _text(0, 6, "液泡", 12, "#5b4f80")))
    parts.append(_text(150, 116, "原生质层", 11.5, "#4a6b90"))
    # 水分子：从细胞里往外跑
    for order in range(7):
        parts.append(_dot(_path_of([(300 - order * 26, 210 + (order % 2) * 30),
                                    (60 + order * 12, 120 + order * 24)]),
                          dur=3.4, begin=round(order * 0.4, 2), r=6, color="#4aa3e0"))
    # 清水（复原）方向的水分子
    for order in range(3):
        parts.append(_dot(_path_of([(70 + order * 30, 300 - order * 20),
                                    (300 - order * 20, 250)]),
                          dur=3.4, begin=round(1.6 + order * 0.5, 2), r=6, color="#7fd4f5"))
    return (_head("质壁分离与复原（仿真：原生质层收缩离开细胞壁）") + "".join(parts)
            + _caption(steps, index, H - 20) + "</svg>")


def _sim_active_transport(steps, index):
    """主动运输：载体蛋白结合分子、消耗 ATP 改变构象，把分子从低浓度一侧送到高浓度一侧
    （逆浓度梯度）。载体在变形，ATP 在消耗。"""
    parts = ['<rect width="%d" height="%d" fill="#f5f8fc"/>' % (W, H)]
    # 膜
    parts.append('<rect x="20" y="150" width="620" height="42" fill="#eaf2fb" '
                 'stroke="#a9c4e2"/>')
    # 载体蛋白：两半各自转 = 构象改变
    parts.append('<g transform="translate(300,171)">'
                 '<g><animateTransform attributeName="transform" type="rotate" '
                 'values="0 0 -26; 0 0 -26; 22 0 -26; 22 0 -26; 0 0 -26" dur="6s" '
                 'keyTimes="0;0.2;0.42;0.8;1" repeatCount="indefinite"/>'
                 '<rect x="-34" y="-40" width="30" height="80" rx="10" fill="#9fd3a8" '
                 'stroke="#3f9e6b" stroke-width="2"/></g>'
                 '<g><animateTransform attributeName="transform" type="rotate" '
                 'values="0 0 26; 0 0 26; -22 0 26; -22 0 26; 0 0 26" dur="6s" '
                 'keyTimes="0;0.2;0.42;0.8;1" repeatCount="indefinite"/>'
                 '<rect x="4" y="-40" width="30" height="80" rx="10" fill="#9fd3a8" '
                 'stroke="#3f9e6b" stroke-width="2"/></g></g>')
    parts.append(_text(300, 232, "载体蛋白（会变形）", 12, "#2f7d52"))
    # 两侧浓度：左边低（少）、右边高（多）
    for order in range(4):
        parts.append('<circle cx="%d" cy="%d" r="6" fill="#4aa3e0"/>'
                     % (70 + order * 34, 120 + (order % 2) * 150))
    for order in range(9):
        parts.append('<circle cx="%d" cy="%d" r="6" fill="#3f6ea8"/>'
                     % (470 + (order % 3) * 46, 96 + (order // 3) * 46))
    parts.append(_text(110, 96, "低浓度", 12, "#1f6ea8"))
    parts.append(_text(560, 96, "高浓度", 12, "#1f3f6e"))
    # 被运输的分子：低浓度侧 -> 载体 -> 高浓度侧
    parts.append(_dot(_path_of([(120, 140), (300, 162)]), dur=6.0, r=8, color="#e08a2b"))
    parts.append(_dot(_path_of([(300, 196), (520, 128)]), dur=6.0, begin=2.5, r=8,
                      color="#e08a2b"))
    # ATP 供能：ATP 进来，ADP + Pi 出去
    parts.append('<g transform="translate(180,260)"><rect x="-32" y="-16" width="64" '
                 'height="32" rx="9" fill="#f0b429" stroke="#c98a12" stroke-width="2">%s'
                 '</rect><text x="0" y="5" font-size="12" fill="#3a4652" '
                 'text-anchor="middle">ATP</text></g>'
                 % _fade("1;1;0;0;1", 6.0, "0;0.25;0.4;0.9;1"))
    parts.append(_dot(_path_of([(212, 268), (300, 268)]), dur=6.0, begin=2.2, r=6,
                      color="#8a5fb0", label="Pi"))
    parts.append(_text(300, 300, "ATP -> ADP + Pi（供能）", 11.5, "#a35a12"))
    parts.append(_text(W / 2, 330, "有特异性、要载体、要消耗 ATP —— 所以能逆浓度梯度运输", 11.5,
                       "#3a4652"))
    return (_head("主动运输（仿真：载体变形，把分子逆浓度梯度送过去）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_enzyme_barrier(steps, index):
    """酶降低活化能：同一条反应，没有酶要翻很高的能垒，有酶走一条低得多的路。
    两颗球分别在两条路上滚。"""
    parts = ['<rect width="%d" height="%d" fill="#f8f9fb"/>' % (W, H)]
    # 坐标轴
    parts.append('<line x1="70" y1="300" x2="620" y2="300" stroke="#3a4652" stroke-width="2"/>')
    parts.append('<line x1="70" y1="300" x2="70" y2="70" stroke="#3a4652" stroke-width="2"/>')
    parts.append(_text(346, 330, "反应进程", 12, "#3a4652"))
    parts.append(_text(96, 62, "能量", 12, "#3a4652"))
    # 无酶：高能垒
    high = [(70, 250), (180, 248), (250, 96), (330, 248), (470, 250), (620, 246)]
    parts.append('<polyline points="%s" fill="none" stroke="#b9c4d0" stroke-width="4" '
                 'stroke-dasharray="8 6"/>' % " ".join("%g,%g" % p for p in high))
    parts.append(_dot(_path_of(high), dur=6.0, r=9, color="#8b98a6"))
    parts.append(_text(250, 80, "无酶：活化能高", 12, "#5b6773"))
    # 有酶：低能垒
    low = [(70, 250), (180, 248), (250, 178), (330, 248), (470, 250), (620, 246)]
    parts.append('<polyline points="%s" fill="none" stroke="#3f9e6b" stroke-width="5"/>'
                 % " ".join("%g,%g" % p for p in low))
    parts.append(_dot(_path_of(low), dur=3.0, r=9, color=GREEN))
    parts.append(_text(250, 162, "有酶：活化能低", 12, "#2f7d52"))
    # 两段活化能箭头
    parts.append(_arrow(152, 248, 152, 110, True, ORANGE))
    parts.append(_text(120, 186, "活化能", 11.5, "#a35a12"))
    parts.append(_arrow(396, 248, 396, 192, True, ORANGE))
    parts.append(_text(432, 216, "活化能（降下来了）", 11, "#a35a12"))
    # 反应物 / 产物
    parts.append('<circle cx="88" cy="250" r="12" fill="#8ab6e0" stroke="#3f6ea8" '
                 'stroke-width="2"/>')
    parts.append(_text(88, 286, "反应物", 11, "#3f6ea8"))
    parts.append('<circle cx="606" cy="246" r="12" fill="#9fd3a8" stroke="#3f9e6b" '
                 'stroke-width="2"/>')
    parts.append(_text(600, 286, "产物", 11, "#2f7d52"))
    return (_head("酶降低活化能（仿真：两颗球在两条路上滚）") + "".join(parts)
            + _caption(steps, index, H - 20) + "</svg>")


def _sim_enzyme_conditions(steps, index):
    """温度与 pH 对酶活性的影响：两条钟形曲线，小球在曲线上跑；高温/过酸过碱处酶变性
    （画的酶形状散开）。"""
    import math
    parts = ['<rect width="%d" height="%d" fill="#f8f9fb"/>' % (W, H)]
    for order, (x0, title, unit) in enumerate(((60, "温度", "最适温度"), (360, "pH", "最适 pH"))):
        parts.append('<line x1="%g" y1="250" x2="%g" y2="250" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x0, x0 + 240))
        parts.append('<line x1="%g" y1="250" x2="%g" y2="76" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x0, x0))
        parts.append(_text(x0 + 120, 66, title + "对酶活性的影响", 12, "#3a4652"))
        points = []
        for step in range(25):
            ratio = step / 24.0
            value = math.exp(-((ratio - 0.5) ** 2) / 0.045)
            points.append((x0 + ratio * 236, 250 - value * 150))
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="4"/>'
                     % (" ".join("%g,%g" % p for p in points), BLUE if order == 0 else GREEN))
        parts.append(_dot(_path_of(points), dur=4.0, r=8, color=ORANGE))
        parts.append('<circle cx="%g" cy="100" r="6" fill="#e0518f"/>'
                     % (x0 + 118, ))
        parts.append(_text(x0 + 118, 292, unit, 11.5, "#a35a12"))
        parts.append(_text(x0 + 34, 96, "低", 11, "#7b8794"))
        parts.append(_text(x0 + 214, 96, "高", 11, "#7b8794"))
    # 右侧：酶的形状（正常 / 变性）
    parts.append('<g transform="translate(560,150)"><circle r="26" fill="#c8e6cf" '
                 'stroke="#3f9e6b" stroke-width="2.4"/>'
                 '<path d="M -12 -18 L 0 4 L 12 -18" fill="none" stroke="#3f9e6b" '
                 'stroke-width="3"/></g>')
    parts.append(_text(560, 118, "正常（能催化）", 11, "#2f7d52"))
    parts.append('<g transform="translate(560,232)"><circle r="26" fill="#f3d6d6" '
                 'stroke="#c96a6a" stroke-width="2.4">%s</circle>'
                 '<path d="M -14 -8 q 8 16 16 0" fill="none" stroke="#c96a6a" '
                 'stroke-width="3">%s</path></g>'
                 % (_pulse(1.6, 0.5), _fade("0;0;1;1;0", 4.0, "0;0.3;0.45;0.85;1")))
    parts.append(_text(560, 276, "变性失活", 11, "#a33f3f"))
    parts.append(_text(W / 2, 330, "低温只是抑制（不变性，回温能恢复）；高温、过酸过碱会让酶变性失活",
                       11.5, "#3a4652"))
    return (_head("温度与 pH 对酶活性的影响（仿真：小球沿活性曲线走）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_atp_adp(steps, index):
    """ATP 与 ADP 的相互转化：ATP 末端高能磷酸键断开 -> 放出能量供生命活动 -> 变成
    ADP + Pi；呼吸作用/光合作用放出的能量再把 ADP 和 Pi 合成 ATP。一直循环。"""
    parts = ['<rect width="%d" height="%d" fill="#fdf8ef"/>' % (W, H)]
    # ATP 分子：腺苷 + 三个磷酸
    parts.append('<rect x="40" y="86" width="86" height="46" rx="12" fill="#f0b429" '
                 'stroke="#c98a12" stroke-width="2"/>')
    parts.append(_text(83, 114, "腺苷", 12, "#3a4652"))
    for order in range(3):
        parts.append('<circle cx="%g" cy="109" r="19" fill="#fdf1cf" stroke="#c98a12" '
                     'stroke-width="2"/>' % (148 + order * 48))
        parts.append(_text(148 + order * 48, 114, "P", 13, "#a3721a"))
    for order in range(2):
        parts.append('<line x1="%g" y1="109" x2="%g" y2="109" stroke="#c98a12" '
                     'stroke-width="6"/>' % (167 + order * 48, 205 + order * 48))
    parts.append('<text x="205" y="86" font-size="11" fill="#c0392b" text-anchor="middle">'
                 '~高能磷酸键</text>')
    # 末端磷酸掉下来 + 能量放出去
    parts.append(_dot(_path_of([(244, 109), (300, 78), (352, 120)]), dur=6.0, r=9,
                      color="#8a5fb0", label="Pi"))
    parts.append('<g transform="translate(300,180)">'
                 '<circle r="16" fill="none" stroke="%s" stroke-width="3">%s</circle>'
                 '<circle r="26" fill="none" stroke="%s" stroke-width="2">%s</circle>'
                 '<circle r="36" fill="none" stroke="%s" stroke-width="1.6">%s</circle></g>'
                 % (ORANGE, _pulse(1.4, 0.4), ORANGE, _pulse(1.4, 0.4, 0.3),
                    ORANGE, _pulse(1.4, 0.4, 0.6)))
    parts.append(_text(300, 240, "放出能量", 12, "#a35a12"))
    # 能量用于生命活动：肌肉收缩（长度在变）
    parts.append('<rect x="392" y="150" width="70" height="30" rx="8" fill="#e08a8a" '
                 'stroke="#c96a6a" stroke-width="2">'
                 '<animate attributeName="width" values="70;44;70" dur="2s" '
                 'repeatCount="indefinite"/></rect>')
    parts.append('<rect x="392" y="196" width="70" height="30" rx="8" fill="#e08a8a" '
                 'stroke="#c96a6a" stroke-width="2">'
                 '<animate attributeName="width" values="70;44;70" dur="2s" '
                 'repeatCount="indefinite"/></rect>')
    parts.append(_text(428, 250, "肌肉收缩 / 主动运输 / 合成反应", 11, "#a33f3f"))
    # ADP + Pi 再合成 ATP（右下角来能量）
    parts.append('<rect x="440" y="88" width="170" height="40" rx="10" fill="#cfe6fb" '
                 'stroke="#7fa8cf" stroke-width="2"/>')
    parts.append(_text(525, 113, "呼吸作用 / 光合作用", 11.5, "#1f6ea8"))
    parts.append(_dot(_path_of([(524, 128), (300, 88)]), dur=6.0, begin=3.0, r=7,
                      color=GREEN))
    parts.append(_text(410, 60, "释放的能量", 11.5, "#2f7d52"))
    # 循环箭头
    parts.append(_arrow(560, 190, 560, 300, False, "#8b98a6"))
    parts.append(_arrow(560, 300, 120, 300, False, "#8b98a6"))
    parts.append(_arrow(120, 300, 120, 150, False, "#8b98a6"))
    parts.append(_text(340, 322, "ATP  ADP + Pi  循环不断（细胞里 ATP 含量少但周转极快）", 11.5,
                       "#3a4652"))
    return (_head("ATP 与 ADP 的相互转化（仿真：高能键断开、能量放出去又收回来）")
            + "".join(parts) + _caption(steps, index, H - 22) + "</svg>")


def _sim_calvin_cycle(steps, index):
    """暗反应（卡尔文循环）：CO2 与 C5 结合生成 2 个 C3；C3 被光反应送来的 ATP、NADPH
    还原成糖，同时再生出 C5 继续固定 CO2。"""
    parts = ['<rect width="%d" height="%d" fill="#f2f9f3"/>' % (W, H)]
    # 左边：类囊体（光反应给的 ATP/NADPH）
    for order in range(3):
        parts.append('<ellipse cx="76" cy="%g" rx="40" ry="11" fill="#9fd3a8" '
                     'stroke="#5aa06d" stroke-width="1.6">%s</ellipse>'
                     % (150 + order * 22, _pulse(1.8, 0.6, order * 0.4)))
    parts.append(_text(76, 120, "类囊体（光反应）", 11.5, "#3f7a51"))
    for order in range(3):
        parts.append(_dot(_path_of([(120, 160), (250, 200)]), dur=2.4,
                          begin=round(order * 0.5, 2), r=6, color=ORANGE))
        parts.append(_dot(_path_of([(126, 176), (256, 216)]), dur=2.8,
                          begin=round(0.3 + order * 0.5, 2), r=5, color=PURPLE))
    parts.append(_text(180, 168, "ATP / NADPH", 11, "#a35a12"))
    # 中间：卡尔文循环（C5 -> C3 -> 糖 + 再生 C5）
    cx, cy, rx, ry = 400, 190, 150, 96
    parts.append('<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="none" stroke="#cfe3d4" '
                 'stroke-width="12"/>' % (cx, cy, rx, ry))
    parts.append('<polygon points="%s" fill="#c8e6cf" stroke="#3f9e6b" stroke-width="2"/>'
                 % " ".join("%g,%g" % (cx - 130 + 20 * __import__("math").cos(a * 3.14159 / 180),
                                       cy + 20 * __import__("math").sin(a * 3.14159 / 180))
                            for a in range(-90, 270, 72)))
    parts.append(_text(cx - 130, cy + 4, "C5", 11.5, "#2f7d52"))
    for order in range(2):
        parts.append('<polygon points="%s" fill="#f6d488" stroke="#d9ab3c" stroke-width="2"/>'
                     % " ".join("%g,%g" % (cx + 100 + 20 * __import__("math").cos(a * 3.14159 / 180),
                                           cy - 20 + order * 44 + 20 * __import__("math").sin(
                                               a * 3.14159 / 180))
                                for a in range(0, 360, 120)))
        parts.append(_text(cx + 100, cy - 16 + order * 44, "C3", 11, "#8a6a1f"))
    parts.append(_dot(_ellipse_path(cx, cy, rx, ry), dur=7.0, r=9, color=GREEN))
    # CO2 进来
    for order in range(2):
        parts.append(_dot(_path_of([(640, 250 - order * 40), (cx - 120, cy + 30)]), dur=3.0,
                          begin=round(order * 0.9, 2), r=6, color="#9aa4ad"))
    parts.append(_text(628, 268, "CO2", 12, "#5b6773"))
    # 糖出去
    for order in range(2):
        parts.append(_dot(_path_of([(cx + 60, cy + 90), (620, 330)]), dur=3.2,
                          begin=round(order * 1.2, 2), r=7, color="#2f9e6b"))
    parts.append(_text(614, 348, "(CH2O) 糖", 11.5, "#2f7d52"))
    # C5 再生（虚线回环）
    parts.append('<path d="M %g %g q 120 40 0 76" fill="none" stroke="#3f9e6b" '
                 'stroke-width="4" stroke-dasharray="10 8">'
                 '<animate attributeName="stroke-dashoffset" values="36;0" dur="2s" '
                 'repeatCount="indefinite"/></path>' % (cx - 60, cy + 70))
    parts.append(_text(cx - 10, cy + 118, "C5 再生（继续固定 CO2）", 11, "#2f7d52"))
    return (_head("暗反应 / 卡尔文循环（仿真：CO2 进来、糖出去、C5 再生）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_mitosis_curve(steps, index):
    """有丝分裂中 DNA 与染色体数目的变化：上面四个小细胞图标在演（间期->中期->后期->末期），
    下面是两条阶梯曲线，小球沿 DNA 曲线走。"""
    parts = ['<rect width="%d" height="%d" fill="#f7f9fc"/>' % (W, H)]
    names = ["间期", "前期·中期", "后期", "末期"]
    for order in range(4):
        x = 60 + order * 150
        on = order == min(index, 3)
        parts.append('<ellipse cx="%g" cy="86" rx="52" ry="38" fill="%s" stroke="%s" '
                     'stroke-width="%g"/>'
                     % (x, "#eaf3ff" if on else "#fff", "#3f6ea8" if on else "#dbe3ec",
                        2.4 if on else 1.4))
        parts.append(_text(x, 140, names[order], 11.5, "#3f6ea8" if on else "#7b8794"))
        # 每个图标里的染色体（位置按分期演）
        offsets = [(-18, -6), (-6, -10), (-6, -6), (-26, -6)]
        for chrom in range(2):
            dx, dy = offsets[order]
            parts.append('<path d="M %g %g Q %g %g %g %g" fill="none" stroke="%s" '
                         'stroke-width="7" stroke-linecap="round"/>'
                         % (x + dx + chrom * 20, 86 + dy - 12,
                            x + dx + chrom * 20 + 6, 86 + dy,
                            x + dx + chrom * 20, 86 + dy + 12,
                            RED if chrom == 0 else BLUE))
        if order == 3:                                    # 末期画成两个细胞
            parts.append('<line x1="%g" y1="50" x2="%g" y2="122" stroke="#3f6ea8" '
                         'stroke-width="2" stroke-dasharray="5 4"/>' % (x, x))
    # 下面：两条阶梯曲线
    parts.append('<line x1="60" y1="300" x2="620" y2="300" stroke="#3a4652" stroke-width="2"/>')
    parts.append('<line x1="60" y1="300" x2="60" y2="176" stroke="#3a4652" stroke-width="2"/>')
    parts.append(_text(340, 330, "细胞周期（间期 -> 前中 -> 后 -> 末）", 11.5, "#3a4652"))
    parts.append(_text(72, 170, "数目", 11.5, "#3a4652"))
    for label, y2, y4, color, dur in (("4N", 216, 186, BLUE, 5.0),
                                      ("2N", 258, 236, ORANGE, 5.0)):
        parts.append(_text(42, y4 + 4, label, 11, color))
    dna = [(80, 258), (200, 258), (240, 186), (450, 186), (500, 258), (600, 258)]
    parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="4"/>'
                 % (" ".join("%g,%g" % p for p in dna), BLUE))
    chrom = [(80, 258), (330, 258), (370, 216), (450, 216), (490, 258), (600, 258)]
    parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="4"/>'
                 % (" ".join("%g,%g" % p for p in chrom), ORANGE))
    parts.append('<circle cx="540" cy="212" r="6" fill="%s"/>' % BLUE)
    parts.append(_text(556, 216, "DNA 数目", 11, BLUE))
    parts.append('<circle cx="540" cy="240" r="6" fill="%s"/>' % ORANGE)
    parts.append(_text(556, 244, "染色体数目", 11, ORANGE))
    parts.append(_dot(_path_of(dna), dur=5.0, r=8, color=GREEN))
    parts.append(_text(W / 2, 332, "间期 DNA 加倍；后期着丝点分裂，染色体数暂时加倍", 11.5,
                       "#3a4652"))
    return (_head("有丝分裂中 DNA 与染色体数目变化（仿真：图标在演、曲线在走）")
            + "".join(parts) + _caption(steps, index, H - 22) + "</svg>")


def _sim_differentiation(steps, index):
    """细胞分化：受精卵有丝分裂出一群相同细胞，这些细胞迁移到不同位置、基因选择性表达，
    变成形态功能各不相同的细胞。画面按段演：只有 index 这一段亮着并脉动，别的段淡出。"""
    import math
    seg = [[], [], []]
    active = min(2, index * 2 // max(1, len(steps) - 1))
    parts = ['<rect width="%d" height="%d" fill="#f6f8fc"/>' % (W, H)]

    # 第 1 段：受精卵有丝分裂，出来一群遗传物质相同的细胞
    seg[0].append('<circle cx="330" cy="88" r="28" fill="#eaf3ff" stroke="#3f6ea8" '
                  'stroke-width="2.4"/>')
    seg[0].append('<circle cx="330" cy="88" r="8" fill="#3f6ea8">%s</circle>'
                  % _pulse(2.0, 0.5))
    seg[0].append(_tag(330, 38, "受精卵（遗传物质相同）", "#3f6ea8"))
    seg[0].append(_arrow(330, 120, 330, 152, True, "#3f6ea8"))
    for order in range(4):
        cx = 168 + order * 108
        seg[0].append('<circle cx="%d" cy="186" r="20" fill="#eaf3ff" stroke="#3f6ea8" '
                      'stroke-width="2">%s</circle>'
                      % (cx, _pulse(1.6, 0.5, round(order * 0.3, 2))))
        seg[0].append('<circle cx="%d" cy="186" r="6" fill="#3f6ea8"/>' % cx)
    seg[0].append(_text(330, 238, "有丝分裂 -> 一群遗传物质相同的细胞", 12, "#3a4652"))

    # 第 2 段：不同基因被选择性表达（同一个核里转录出不同的 mRNA）
    seg[1].append('<circle cx="330" cy="186" r="48" fill="#eaf3ff" stroke="#3f6ea8" '
                  'stroke-width="2.4"/>')
    seg[1].append('<circle cx="330" cy="186" r="21" fill="#cfe6fb" stroke="#3f6ea8" '
                  'stroke-width="1.8"/>')
    seg[1].append('<path d="M 320 180 q 10 -12 16 2 q 6 14 -12 12" fill="none" '
                  'stroke="#8a5fb0" stroke-width="3" stroke-linecap="round">%s</path>'
                  % _pulse(2.0, 0.45))
    seg[1].append(_tag(330, 112, "遗传物质相同，基因选择性表达", "#5b4f77"))
    for order, (x, y, color, name) in enumerate(
            ((122, 128, "#8a5fb0", "基因 A 表达"), (538, 128, "#3f9e6b", "基因 B 表达"),
             (122, 250, "#e0518f", "基因 C 表达"), (538, 250, "#e08a2b", "基因 D 表达"))):
        curve = "M 330 186 Q 330 %g %g %g" % (y, x, y)
        seg[1].append('<path d="%s" fill="none" stroke="%s" stroke-width="3.4" '
                      'stroke-dasharray="7 5"/>' % (curve, color))
        seg[1].append(_dot(curve, dur=2.6, begin=round(order * 0.5, 2), r=6, color=color))
        seg[1].append(_tag(x, y - 24, name, color))
    seg[1].append(_text(330, 324, "同一次有丝分裂来的细胞，遗传物质相同；不同基因被选择性表达",
                        11.5, "#3a4652"))

    # 第 3 段：结果——形态、结构、功能都不同的四种细胞
    targets = [(110, 200, "神经细胞", "#8a5fb0", 0.0), (300, 210, "肌肉细胞", "#d98a8a", 0.9),
               (460, 190, "上皮细胞", "#3f9e6b", 1.8), (580, 250, "红细胞", "#c0392b", 2.7)]
    for order, (x, y, name, color, delay) in enumerate(targets):
        seg[2].append('<g transform="translate(%g,%g)">' % (x, y))
        if order == 0:                                          # 神经细胞：胞体 + 树突 + 轴突
            seg[2].append('<circle r="16" fill="#f0e8fa" stroke="%s" stroke-width="2"/>'
                          % color)
            for angle in (-60, -20, 20, 60):
                seg[2].append('<line x1="0" y1="0" x2="%g" y2="%g" stroke="%s" '
                              'stroke-width="3"/>'
                              % (34 * math.cos(angle * 3.14159 / 180),
                                 -34 * math.sin(angle * 3.14159 / 180), color))
            seg[2].append('<line x1="0" y1="0" x2="-58" y2="34" stroke="%s" '
                          'stroke-width="4"/>' % color)
        elif order == 1:                                        # 肌肉细胞：长梭形
            seg[2].append('<ellipse rx="54" ry="15" fill="#f7dede" stroke="%s" '
                          'stroke-width="2">%s</ellipse>' % (color, _pulse(1.8, 0.6)))
        elif order == 2:                                        # 上皮细胞：紧密排列
            for row in range(2):
                for col in range(3):
                    seg[2].append('<rect x="%g" y="%g" width="26" height="18" rx="4" '
                                  'fill="#e7f3ec" stroke="%s" stroke-width="1.8"/>'
                                  % (-42 + col * 28, -20 + row * 20, color))
        else:                                                   # 红细胞：双凹圆盘
            seg[2].append('<circle r="18" fill="#f6d6d6" stroke="%s" stroke-width="2.4"/>'
                          % color)
            seg[2].append('<circle r="8" fill="#ffffff" stroke="%s" stroke-width="1.6"/>'
                          % color)
        seg[2].append(_text(0, 52, name, 11.5, color))
        seg[2].append('</g>')
    # 相同的细胞从中间迁移过去（每条路上一个细胞在跑）
    for order, (x, y, name, color, delay) in enumerate(targets):
        seg[2].append('<g>%s<circle r="11" fill="#eaf3ff" stroke="#3f6ea8" stroke-width="2"/>'
                      '<circle r="4" fill="%s">%s</circle></g>'
                      % (_motion(_path_of([(330, 118), (x, y - 34)]), dur=6.0, begin=delay),
                         color, _pulse(1.6, 0.4, delay)))
    seg[2].append(_text(W / 2, 330, "结果：形态、结构、功能都不同的细胞群 —— 这就是细胞分化",
                        11.5, "#3a4652"))

    # 只有本段亮着并脉动，别的段淡出——一眼看出这张图在演哪一步
    for order, body in enumerate(seg):
        on = order == active
        parts.append('<g opacity="%g">%s%s</g>'
                     % (1 if on else 0.24, _pulse(1.8, 0.6) if on else "", "".join(body)))
    return (_head("细胞分化（仿真：相同细胞走出去，变成四种不同的细胞）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")



def _sim_transcription(steps, index):
    """转录：**DNA 往左走、RNA 聚合酶固定在画面中间**（和真实动画一样），
    它把双链撑开一个解链泡，身后拖出一条越来越长的 mRNA；核糖核苷酸从上方落进泡里配对。"""
    parts = ['<rect width="%d" height="%d" fill="#f7f5fb"/>' % (W, H)]
    parts.append(_text(110, 52, "DNA（双链）", 12, "#3f6ea8"))
    scroll = ('<animateTransform attributeName="transform" type="translate" '
              'values="0,0; -20,0" dur="0.9s" repeatCount="indefinite"/>')
    ladder = ['<line x1="-300" y1="150" x2="760" y2="150" stroke="#3f6ea8" stroke-width="8"/>',
              '<line x1="-300" y1="188" x2="760" y2="188" stroke="#8a5fb0" stroke-width="8"/>']
    for spot in range(-300, 760, 20):
        ladder.append('<line x1="%d" y1="150" x2="%d" y2="188" stroke="#c3cede" '
                      'stroke-width="3"/>' % (spot, spot))
    parts.append('<g><g>%s%s</g></g>' % (scroll, "".join(ladder)))
    # mRNA：从聚合酶下缘往左下延伸（揭示动画 = 一边转录一边变长）
    parts.append('<path d="M 312 196 C 250 246 168 268 56 288" fill="none" '
                 'stroke="#e0518f" stroke-width="6" stroke-linecap="round" '
                 'stroke-dasharray="330 330">'
                 '<animate attributeName="stroke-dashoffset" values="250;0" dur="5s" '
                 'repeatCount="indefinite"/></path>')
    parts.append(_tag(186, 234, "mRNA（在聚合酶身后长出来）", "#b03070"))
    # 聚合酶（固定）+ 解链泡：泡盖住横档，表示这里双链被撑开了
    parts.append('<g transform="translate(330,169)">'
                 '<rect x="-70" y="-52" width="140" height="104" rx="26" fill="#ffffff" '
                 'opacity="0.97" stroke="#c98a12" stroke-width="2" stroke-dasharray="7 5"/>'
                 '<path d="M -40 -34 q 40 26 80 0 L 40 34 q -40 -26 -80 0 Z" fill="#f0a92b" '
                 'stroke="#c98a12" stroke-width="2"/>%s</g>'
                 % _text(0, 5, "RNA 聚合酶", 11, "#7a5205"))
    parts.append(_tag(452, 108, "解链泡（双链被撑开）", "#a3721a"))
    # 核糖核苷酸：从上方落进泡里（落地淡出）
    colors = ["#d94f4f", "#3f9e6b", "#3f6ea8", "#8a5fb0"]
    for order in range(4):
        parts.append(_dot(_path_of([(296 + order * 20, 78), (300 + order * 20, 122)]),
                          dur=1.4, begin=round(order * 0.55, 2), r=6.5,
                          color=colors[order % 4]))
    for order in range(4):
        parts.append('<circle cx="%d" cy="%d" r="6.5" fill="%s">%s</circle>'
                     % (482 + order * 24, 78, colors[order], _pulse(1.8, 0.6, order * 0.3)))
    parts.append(_tag(556, 104, "四种核糖核苷酸", "#5b6773"))
    # 走到头：mRNA 脱落去细胞质
    parts.append('<g>%s<path d="M -26 0 q 12 -10 26 -5 q 14 5 26 0" fill="none" '
                 'stroke="#e0518f" stroke-width="6" stroke-linecap="round">%s</path></g>'
                 % (_motion(_path_of([(60, 292), (40, 342)]), dur=9.0),
                    _fade("0;0;0;1;0", 9.0, "0;0.82;0.9;0.97;1")))
    parts.append(_text(W / 2, 336, "A—U、T—A、G—C 配对；mRNA 会去细胞质里指导翻译", 12,
                       "#3a4652"))
    return (_head("转录（仿真：DNA 往左走，聚合酶固定撑开双链，身后长出 mRNA）")
            + "".join(parts) + _caption(steps, index, H - 26) + "</svg>")


def _sim_translation(steps, index):
    """翻译：**mRNA 一个密码子一个密码子地往左挪，核糖体固定在画面中间**，
    每个密码子到位就有一个 tRNA 送氨基酸来配对，氨基酸在核糖体里连成肽链往左边长出去。"""
    parts = ['<rect width="%d" height="%d" fill="#f5faf6"/>' % (W, H)]
    codons = ["AUG", "GCU", "UAC", "GGA", "CUG", "UAA"]
    mrna = ['<line x1="-320" y1="252" x2="900" y2="252" stroke="#e0518f" stroke-width="9"/>']
    for k in range(-4, 12):
        x = k * 80
        mrna.append('<line x1="%d" y1="243" x2="%d" y2="261" stroke="#ffffff" '
                    'stroke-width="2.5"/>' % (x, x))
        name = codons[k % 6]
        mrna.append(_text(x + 40, 280, name, 11,
                          "#c0392b" if name == "UAA" else "#b03070"))
    # 一次挪一个密码子（停一下再挪，像真的转位）
    times, values = [0.0], ["0,0"]
    for k in range(1, 7):
        times.append(round(0.17 * k - 0.03, 3))
        values.append("%d,0" % (-80 * (k - 1)))
        times.append(round(0.17 * k, 3))
        values.append("%d,0" % (-80 * k))
    step = ('<animateTransform attributeName="transform" type="translate" dur="12s" '
            'repeatCount="indefinite" values="%s" keyTimes="%s"/>'
            % ("; ".join(values), "; ".join("%g" % t for t in times)))
    parts.append('<g><g>%s%s</g></g>' % (step, "".join(mrna)))
    parts.append(_tag(560, 306, "mRNA（密码子）", "#b03070"))
    # 核糖体（固定，夹着 mRNA）
    parts.append('<g transform="translate(330,206)">'
                 '<ellipse cx="0" cy="-30" rx="44" ry="25" fill="#9fd3a8" '
                 'stroke="#5aa06d" stroke-width="2"/>'
                 '<ellipse cx="0" cy="32" rx="48" ry="27" fill="#c8e6cf" '
                 'stroke="#5aa06d" stroke-width="2"/>%s</g>'
                 % _text(0, 36, "核糖体", 11, "#2f6a44"))
    # 肽链：从核糖体左下方长出去（揭示动画，越长越长）
    parts.append('<path d="M 286 -0 L 246 22 L 200 16 L 154 34 L 108 26 L 66 44" '
                 'fill="none" stroke="#e08a2b" stroke-width="7" stroke-linecap="round" '
                 'stroke-dasharray="240 240">'
                 '<animate attributeName="stroke-dashoffset" values="240;0;0" dur="12s" '
                 'keyTimes="0;0.8;1" repeatCount="indefinite"/></path>')
    for k in range(6):
        x = 286 - k * 44
        y = 22 if k % 2 else 16
        parts.append('<circle cx="%d" cy="%d" r="7.5" fill="#f0b429" stroke="#c98a12">'
                     '<animate attributeName="opacity" values="0;0;1;1" dur="12s" '
                     'keyTimes="0;%g;%g;1" repeatCount="indefinite"/></circle>'
                     % (x, y - k * 1.5, min(0.79, 0.1 + k * 0.12),
                        min(0.8, 0.12 + k * 0.12)))
    parts.append(_tag(188, 96, "肽链（氨基酸接上去）", "#a35a12"))
    # tRNA：每 2 秒一个，落到固定的那个密码子位上
    for order in range(5):
        parts.append('<g>%s<path d="M -15 0 L 0 -17 L 15 0 Z" fill="#8a6cd0"/>'
                     '<circle cx="0" cy="-26" r="9.5" fill="#f0b429" stroke="#c98a12"/>'
                     '<text x="0" y="17" font-size="10" fill="#6a4fb0" '
                     'text-anchor="middle">tRNA</text>%s</g>'
                     % (_motion(_path_of([(330, 350), (330, 268)]), dur=0.9,
                                begin=round(1.2 + order * 2.0, 2)),
                        _fade("0;0;1;1;0", 2.0, "0;0.05;0.2;0.75;1")))
    parts.append(_tag(200, 340, "tRNA 带氨基酸送到核糖体", "#6a4fb0"))
    # 终止：肽链脱落折叠成蛋白质
    parts.append('<g>%s<path d="M -26 0 q 26 -30 52 0 q -26 30 -52 0 Z" fill="#e08a2b" '
                 'stroke="#c96a12" stroke-width="2">%s</path></g>'
                 % (_motion(_path_of([(60, 44), (48, 336)]), dur=12.0),
                    _fade("0;0;0;1;0", 12.0, "0;0.8;0.86;0.95;1")))
    parts.append(_tag(64, 320, "蛋白质", "#a35a12"))
    parts.append(_text(560, 122, "读到 UAA 就停", 12, "#c0392b"))
    return (_head("翻译（仿真：mRNA 一个密码子一个密码子地挪，核糖体不动）")
            + "".join(parts) + _caption(steps, index, H - 26) + "</svg>")


def _sim_osmosis(steps, index):
    """渗透作用：半透膜两侧浓度不同，水分子穿过膜往高浓度一侧跑，那一侧液面上升，
    溶质分子过不去。液面高度是一直在变的。"""
    parts = ['<rect width="%d" height="%d" fill="#f4f9ff"/>' % (W, H)]
    # 两侧液面（高度用 SMIL 动画：右侧升、左侧降）
    parts.append('<rect x="40" y="150" width="260" height="180" fill="#dceffb" '
                 'stroke="#8fbbe0" stroke-width="2">'
                 '<animate attributeName="y" values="150;110;150" dur="6s" '
                 'repeatCount="indefinite"/>'
                 '<animate attributeName="height" values="180;220;180" dur="6s" '
                 'repeatCount="indefinite"/></rect>')
    parts.append('<rect x="360" y="110" width="260" height="220" fill="#cfe6fb" '
                 'stroke="#8fbbe0" stroke-width="2">'
                 '<animate attributeName="y" values="110;70;110" dur="6s" '
                 'repeatCount="indefinite"/>'
                 '<animate attributeName="height" values="220;260;220" dur="6s" '
                 'repeatCount="indefinite"/></rect>')
    # 半透膜（中间一条带小孔的竖线）
    for spot in range(96, 340, 18):
        parts.append('<circle cx="330" cy="%d" r="5" fill="#ffffff" stroke="#7fa8cf"/>' % spot)
    parts.append('<rect x="322" y="90" width="16" height="264" fill="none" stroke="#7fa8cf" '
                 'stroke-width="3"/>')
    parts.append(_text(330, 84, "半透膜", 11.5, "#4a6b90"))
    parts.append(_text(150, 96, "低浓度", 12, "#1f6ea8"))
    parts.append(_text(510, 96, "高浓度", 12, "#a35a12"))
    # 溶质分子：只在右侧，过不去
    for order in range(9):
        parts.append('<circle cx="%d" cy="%d" r="7" fill="#e08a2b" stroke="#c96a12">%s</circle>'
                     % (390 + (order % 3) * 70, 150 + (order // 3) * 60,
                        _pulse(2.0, 0.7, order * 0.2)))
    # 水分子：一直在穿膜（左 -> 右），净移动朝高浓度一侧
    for order in range(6):
        parts.append(_dot(_path_of([(150 + order * 20, 320), (470, 190)]), dur=3.0,
                          begin=round(order * 0.5, 2), r=6, color="#4aa3e0"))
    parts.append(_text(W / 2, 60, "水分子（蓝）能过膜，溶质（橙）过不去", 12, "#3a4652"))
    parts.append(_text(W / 2, 330, "单位时间内进入高浓度一侧的水更多 —— 那一侧液面上升", 11.5,
                       "#3a4652"))
    return (_head("渗透作用（仿真：水分子穿过半透膜，液面跟着升降）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_action_potential(steps, index):
    """动作电位：刺激后 Na+ 通道打开、Na+ 内流（去极化）；随后 K+ 通道打开、K+ 外流
    （复极化）；下面同时画膜电位曲线，游标跟着走。"""
    parts = ['<rect width="%d" height="%d" fill="#f6f7fb"/>' % (W, H)]
    # 轴突膜（上下两条线，中间是膜内）
    parts.append('<rect x="20" y="150" width="620" height="34" fill="#eaf2fb" '
                 'stroke="#a9c4e2"/>')
    parts.append(_text(44, 140, "膜外", 11.5, "#4a6b90"))
    parts.append(_text(44, 206, "膜内", 11.5, "#4a6b90"))
    # 三种通道
    for order, (cx, name, color) in enumerate(((170, "Na+ 通道", "#d94f4f"),
                                               (330, "K+ 通道", "#8a5fb0"),
                                               (490, "钠钾泵", "#3f9e6b"))):
        parts.append('<rect x="%g" y="142" width="34" height="50" rx="8" fill="%s" '
                     'stroke="#5b6773" stroke-width="1.6">%s</rect>'
                     % (cx, color, _pulse(1.6, 0.45, order * 0.4)))
        parts.append(_text(cx + 17, 216, name, 11.5, "#3a4652"))
    # 离子流：Na+ 往里冲，K+ 往外跑
    for order in range(4):
        parts.append(_dot(_path_of([(178 + order * 12, 96), (178 + order * 12, 186)]),
                          dur=1.6, begin=round(order * 0.4, 2), r=6, color="#d94f4f"))
    for order in range(4):
        parts.append(_dot(_path_of([(338 + order * 12, 186), (338 + order * 12, 96)]),
                          dur=1.8, begin=round(0.6 + order * 0.4, 2), r=6, color="#8a5fb0"))
    parts.append(_text(300, 88, "Na+ 内流（去极化）", 11.5, "#a33f3f"))
    parts.append(_text(520, 88, "K+ 外流（复极化）", 11.5, "#6a4fb0"))
    # 膜电位曲线（手画 + 游标小球）
    curve = [(60, 300), (120, 300), (140, 288), (170, 232), (200, 232), (225, 262),
             (250, 296), (270, 302), (300, 296), (620, 296)]
    parts.append('<polyline points="%s" fill="none" stroke="#3f6ea8" stroke-width="3"/>'
                 % " ".join("%g,%g" % (x, y) for x, y in curve))
    parts.append(_dot(_path_of(curve), dur=6.0, r=7, color=ORANGE))
    parts.append(_text(300, 268, "膜电位曲线（-70 mV 静息 -> +30 mV 动作电位）", 11.5,
                       "#3f6ea8"))
    parts.append(_text(W / 2, 330, "全或无：刺激够强就产生固定幅度的动作电位，再强只增加频率",
                       11.5, "#3a4652"))
    return (_head("动作电位（仿真：离子通道开闭，离子进出，电位跟着变）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_pcr(steps, index):
    """PCR：升温变性（双链解开）-> 降温退火（引物结合）-> 适温延伸（聚合酶沿引物延长），
    循环一次 DNA 数量翻一倍。"""
    parts = ['<rect width="%d" height="%d" fill="#f7f6f2"/>' % (W, H)]
    # 温度循环条：三段依次亮
    for order, (x, name, color) in enumerate(((40, "94 度 变性", "#d94f4f"),
                                              (250, "55 度 退火", "#3f9e6b"),
                                              (460, "72 度 延伸", "#3f6ea8"))):
        parts.append('<rect x="%g" y="52" width="170" height="46" rx="10" fill="%s" '
                     'stroke="#5b6773" stroke-width="1.4">%s</rect>'
                     % (x, color, _pulse(6.0, 0.28, order * 2.0)))
        parts.append(_text(x + 85, 80, name, 12.5, "#ffffff"))
    # 循环箭头
    parts.append(_arrow(556, 76, 636, 76, False, "#8b98a6"))
    parts.append(_arrow(636, 76, 636, 130, False, "#8b98a6"))
    parts.append(_arrow(636, 130, 40, 130, False, "#8b98a6"))
    parts.append(_arrow(40, 130, 40, 104, False, "#8b98a6"))
    parts.append(_text(600, 158, "循环 30 次", 11.5, "#5b6773"))
    # 模板 DNA：变性时两条链分开（左右平移），退火时引物贴上，延伸时长出新链
    parts.append('<g transform="translate(330,232)">'
                 '<g>%s<path d="M -150 0 L 150 0" stroke="#3f6ea8" stroke-width="8" '
                 'stroke-linecap="round"/></g>'
                 '<g>%s<path d="M -150 26 L 150 26" stroke="#8a5fb0" stroke-width="8" '
                 'stroke-linecap="round"/></g>'
                 '<g>%s<rect x="-42" y="-6" width="34" height="12" rx="6" fill="#3f9e6b"/>'
                 '<rect x="26" y="32" width="34" height="12" rx="6" fill="#3f9e6b"/></g>'
                 '</g>'
                 % (_move("0,0; 0,-24; 0,-24; 0,0", 6.0, "0;0.33;0.66;1"),
                    _move("0,0; 0,24; 0,24; 0,0", 6.0, "0;0.33;0.66;1"),
                    _fade("0;0;1;1;0", 6.0, "0;0.3;0.36;0.8;1")))
    # 新链在延伸（虚线动画）
    for order, y in enumerate((218, 268)):
        parts.append('<path d="M %g %g L %g %g" stroke="#e08a2b" stroke-width="6" '
                     'stroke-dasharray="14 10" stroke-linecap="round">'
                     '<animate attributeName="stroke-dashoffset" values="48;0" dur="2s" '
                     'repeatCount="indefinite"/>%s</path>'
                     % (200, y, 470, y, _fade("0;0;0;1;0", 6.0, "0;0.5;0.62;0.9;1")))
    parts.append(_text(330, 316, "引物（绿）结合 -> 耐高温聚合酶沿引物延长新链（橙）", 11.5,
                       "#3a4652"))
    parts.append(_text(330, 332, "循环 1 次变 2 份、2 次 4 份…… 30 次约 10 亿倍", 11.5,
                       "#3a4652"))
    return (_head("PCR（仿真：变性 -> 退火 -> 延伸，一轮轮循环）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_secretory(steps, index):
    """分泌蛋白：核糖体把氨基酸连成肽链 -> 内质网加工折叠 -> 囊泡送到高尔基体再加工
    -> 囊泡到细胞膜融合，把蛋白质分泌到细胞外。"""
    parts = ['<rect width="%d" height="%d" fill="#f7f9fc"/>' % (W, H)]
    parts.append('<ellipse cx="330" cy="196" rx="300" ry="126" fill="#f2f7fd" '
                 'stroke="#7fa8cf" stroke-width="3"/>')
    parts.append(_text(60, 110, "细胞膜", 11.5, "#4a6b90"))
    parts.append('<circle cx="120" cy="220" r="34" fill="#e6e0f5" stroke="#8a7fb0" '
                 'stroke-width="2"/>')
    parts.append(_text(120, 224, "细胞核", 11.5, "#5b4f80"))
    # 内质网（波浪线 + 核糖体小点）
    parts.append('<path d="M 180 150 q 30 -22 60 0 q 30 22 60 0 q 30 -22 60 0" fill="none" '
                 'stroke="#8a6cd0" stroke-width="5" stroke-linecap="round"/>')
    parts.append('<path d="M 180 186 q 30 -22 60 0 q 30 22 60 0 q 30 -22 60 0" fill="none" '
                 'stroke="#8a6cd0" stroke-width="5" stroke-linecap="round"/>')
    for spot in range(186, 356, 18):
        parts.append('<circle cx="%d" cy="%d" r="5" fill="#e08a2b">%s</circle>'
                     % (spot, 138 if (spot // 18) % 2 else 174, _pulse(1.8, 0.6)))
    parts.append(_text(240, 118, "内质网（附着的核糖体）", 11.5, "#6a4fb0"))
    # 高尔基体（叠起来的弧）
    for order in range(4):
        parts.append('<path d="M 420 %g q 46 -16 92 0" fill="none" stroke="#3f9e6b" '
                     'stroke-width="7" stroke-linecap="round">%s</path>'
                     % (146 + order * 20, _pulse(2.0, 0.6, order * 0.3)))
    parts.append(_text(466, 118, "高尔基体", 11.5, "#2f7d52"))
    # 肽链 -> 囊泡 -> 高尔基体 -> 细胞膜 -> 分泌出去
    for order in range(3):
        parts.append(_dot(_path_of([(300, 168), (420, 190)]), dur=2.2,
                          begin=round(order * 0.7, 2), r=8, color="#e08a2b"))
    for order in range(3):
        parts.append(_dot(_path_of([(500, 200), (560, 236)]), dur=2.0,
                          begin=round(order * 0.6, 2), r=8, color="#3f9e6b"))
    for order in range(3):
        parts.append(_dot(_path_of([(600, 250), (640, 300)]), dur=2.4,
                          begin=round(order * 0.8, 2), r=8, color="#f0b429"))
    parts.append(_text(316, 300, "囊泡：内质网 -> 高尔基体 -> 细胞膜", 11.5, "#3a4652"))
    parts.append(_text(614, 322, "分泌到细胞外", 11.5, "#a3721a"))
    # 膜面积变化提示
    parts.append(_text(W / 2, 330, "膜面积：内质网减小、高尔基体先增后减、细胞膜增大", 11.5,
                       "#3a4652"))
    return (_head("分泌蛋白的合成与运输（仿真：囊泡一站站送出去）") + "".join(parts)
            + _caption(steps, index, H - 22) + "</svg>")


def _sim_nuclear_transfer(steps, index):
    """核移植（克隆动物）：取供体细胞的核 -> 卵母细胞去核 -> 注入形成重组细胞 -> 电刺激激活、
    胚胎移植 -> 克隆动物。画面按段演：只有 index 这一段亮着并脉动，别的段淡出。全过程都在动。"""
    seg = [[], [], [], []]
    active = min(3, index * 3 // max(1, len(steps) - 1))
    parts = ['<rect width="%d" height="%d" fill="#f7f6fb"/>' % (W, H)]

    # 第 1 段：把供体细胞的细胞核取出来
    seg[0].append('<circle cx="336" cy="196" r="62" fill="#fdecec" stroke="#c96a6a" '
                  'stroke-width="2.6"/>')
    seg[0].append('<circle cx="336" cy="196" r="20" fill="#b48ad6" stroke="#7a4fb0" '
                  'stroke-width="2">%s</circle>' % _pulse(1.6, 0.55))
    seg[0].append('<path d="M 40 158 L 252 180 L 252 214 L 40 236 Z" fill="#dfe6ee" '
                  'stroke="#9aa8b8" stroke-width="2"/>')
    seg[0].append(_text(146, 144, "显微吸管", 11.5, "#5b6773"))
    seg[0].append('<g>%s<circle r="18" fill="#b48ad6" stroke="#7a4fb0" stroke-width="2"/></g>'
                  % _motion(_path_of([(336, 196), (168, 198)]), dur=5.0))
    seg[0].append(_tag(336, 96, "供体细胞（提供细胞核）", "#a33f3f"))
    seg[0].append(_tag(336, 300, "① 取出供体细胞的细胞核", "#7a4fb0"))

    # 第 2 段：卵母细胞去核（把它的核吸走）
    seg[1].append('<circle cx="356" cy="192" r="66" fill="#e9f4fd" stroke="#6f9dc9" '
                  'stroke-width="3"/>')
    seg[1].append('<circle cx="356" cy="192" r="20" fill="#b48ad6" stroke="#7a4fb0" '
                  'stroke-width="2">%s</circle>' % _pulse(1.6, 0.55))
    seg[1].append('<path d="M 40 158 L 262 180 L 262 214 L 40 236 Z" fill="#dfe6ee" '
                  'stroke="#9aa8b8" stroke-width="2"/>')
    seg[1].append(_text(150, 144, "把核吸出去的吸管", 11.5, "#5b6773"))
    seg[1].append('<g>%s<circle r="18" fill="#b48ad6" stroke="#7a4fb0" stroke-width="2"/></g>'
                  % _motion(_path_of([(356, 192), (176, 198)]), dur=5.0))
    seg[1].append(_tag(356, 96, "卵母细胞", "#3f6ea8"))
    seg[1].append(_tag(356, 300, "② 卵母细胞去核（去掉细胞核）", "#3f6ea8"))

    # 第 3 段：把供体核注入去核卵母细胞，形成重组细胞
    seg[2].append('<circle cx="330" cy="200" r="66" fill="#eef7ff" stroke="#6f9dc9" '
                  'stroke-width="3" stroke-dasharray="9 6"/>')
    seg[2].append('<circle cx="330" cy="82" r="32" fill="#fdecec" stroke="#c96a6a" '
                  'stroke-width="2.6"/>')
    seg[2].append('<circle cx="330" cy="82" r="12" fill="#b48ad6" stroke="#7a4fb0" '
                  'stroke-width="2">%s</circle>' % _pulse(1.6, 0.55))
    seg[2].append(_tag(330, 36, "供体细胞的核", "#a33f3f"))
    seg[2].append('<g>%s<circle r="18" fill="#b48ad6" stroke="#7a4fb0" stroke-width="2"/></g>'
                  % _motion(_path_of([(330, 108), (330, 192)]), dur=5.0))
    seg[2].append('<circle cx="330" cy="200" r="30" fill="none" stroke="#7a4fb0" '
                  'stroke-width="2" stroke-dasharray="6 5"/>')
    seg[2].append(_tag(330, 300, "③ 注入后形成重组细胞（核来自供体）", "#7a4fb0"))

    # 第 4 段：电刺激激活 -> 早期胚胎 -> 移植到代孕母体 -> 克隆动物
    seg[3].append('<rect x="116" y="118" width="13" height="48" rx="5" fill="#8b98a6"/>')
    seg[3].append('<rect x="116" y="236" width="13" height="48" rx="5" fill="#8b98a6"/>')
    seg[3].append(_text(122, 106, "+", 14, "#c0392b"))
    seg[3].append(_text(122, 300, "-", 14, "#1f6ea8"))
    seg[3].append('<path d="M 122 172 L 100 198 L 116 198 L 96 232" fill="none" '
                  'stroke="#f0b429" stroke-width="4" stroke-linecap="round">%s</path>'
                  % _pulse(1.2, 0.3))
    seg[3].append('<circle cx="252" cy="196" r="54" fill="#eef7ff" stroke="#6f9dc9" '
                  'stroke-width="3"/>')
    seg[3].append('<circle cx="252" cy="196" r="13" fill="#b48ad6" stroke="#7a4fb0" '
                  'stroke-width="2">%s</circle>' % _pulse(1.4, 0.35))
    seg[3].append(_tag(252, 100, "④ 电刺激激活", "#a3721a"))
    for order in range(4):
        seg[3].append('<circle cx="%d" cy="%d" r="11" fill="#c8e6cf" stroke="#3f9e6b" '
                      'stroke-width="2">%s</circle>'
                      % (500 + (order % 2) * 26, 146 + (order // 2) * 26,
                         _fade("0;0;1;1;0", 6.0,
                               "0;%g;%g;0.9;1" % (0.1 + order * 0.12,
                                                  0.16 + order * 0.12))))
    seg[3].append(_tag(513, 110, "早期胚胎（细胞不断分裂）", "#2f7d52"))
    seg[3].append('<g>%s<circle r="10" fill="#c8e6cf" stroke="#3f9e6b" stroke-width="2"/></g>'
                  % _motion(_path_of([(513, 200), (300, 280)]), dur=5.0))
    seg[3].append('<g transform="translate(268,304)">'
                  '<ellipse cx="0" cy="0" rx="52" ry="24" fill="#f3e6d2" stroke="#b99a6a" '
                  'stroke-width="2.4"/>'
                  '<circle cx="52" cy="-14" r="15" fill="#f3e6d2" stroke="#b99a6a" '
                  'stroke-width="2.4"/></g>')
    seg[3].append(_text(268, 338, "胚胎移植到代孕母体", 11.5, "#8a6a1f"))
    seg[3].append('<g transform="translate(512,304)">'
                  '<ellipse cx="0" cy="0" rx="34" ry="16" fill="#fdf6ea" stroke="#b99a6a" '
                  'stroke-width="2.2">%s</ellipse>'
                  '<circle cx="34" cy="-9" r="10" fill="#fdf6ea" stroke="#b99a6a" '
                  'stroke-width="2.2"/></g>'
                  % _fade("0;0;0;1;1", 6.0, "0;0.75;0.86;0.95;1"))
    seg[3].append(_tag(512, 262, "克隆动物（性状与供体核一致）", "#a35a12"))

    # 只有本段亮着并脉动，别的段淡出
    for order, body in enumerate(seg):
        on = order == active
        parts.append('<g opacity="%g">%s%s</g>'
                     % (1 if on else 0.24, _pulse(1.8, 0.6) if on else "", "".join(body)))
    return (_head("核移植（仿真：吸核 -> 注入 -> 电激 -> 胚胎移植 -> 克隆动物）")
            + "".join(parts) + _caption(steps, index, H - 22) + "</svg>")





#: 这些过程的图片比较抽象，图下面**再放一段仿真动画**（图文一起看）
MEDIA_PLUS_ANIM = {"light_reaction", "calvin_cycle", "atp_adp",
                   "cell_differentiation", "internal_environment",
                   "humoral_immunity", "cellular_immunity"}
def _sim_internal_environment(steps, index):
    """内环境的组成关系：血浆 -> 组织液 -> 淋巴 -> 血浆，物质一直在交换，细胞泡在组织液里。
    画面按段演：只有 index 这一段亮着并脉动；三种"场所"的轮廓是公共背景，保持正常。"""
    seg = [[], [], [], []]
    active = min(3, index * 3 // max(1, len(steps) - 1))
    parts = ['<rect width="%d" height="%d" fill="#f6f9fd"/>' % (W, H)]
    # 公共背景：三种场所的轮廓（毛细血管 / 组织液 / 毛细淋巴管）
    parts.append('<rect x="24" y="60" width="612" height="76" rx="34" fill="#fdecec" '
                 'stroke="#c96a6a" stroke-width="3"/>')
    parts.append(_text(96, 48, "毛细血管（血浆）", 12, "#a33f3f"))
    parts.append('<rect x="24" y="176" width="440" height="150" rx="16" fill="#eaf6ff" '
                 'stroke="#7fa8cf" stroke-width="2" stroke-dasharray="8 5"/>')
    parts.append(_text(120, 168, "组织液", 12, "#1f6ea8"))
    parts.append('<path d="M 500 190 q 60 34 0 70 q -60 34 0 66" fill="none" '
                 'stroke="#3f9e6b" stroke-width="10" stroke-linecap="round"/>')
    parts.append(_text(566, 200, "毛细淋巴管（淋巴）", 11.5, "#2f7d52"))

    # 第 1 段：血浆——血细胞生活的环境
    seg[0].append('<rect x="24" y="60" width="612" height="76" rx="34" fill="none" '
                  'stroke="#e0518f" stroke-width="3" stroke-dasharray="12 8"/>')
    seg[0].append(_tag(330, 160, "① 血浆：血细胞生活的环境", "#b03070"))
    for order in range(5):
        seg[0].append('<ellipse cx="%d" cy="98" rx="17" ry="13" fill="#e0518f" '
                      'stroke="#b03070" stroke-width="1.6">%s</ellipse>'
                      % (120 + order * 110, _pulse(2.2, 0.6, round(order * 0.3, 2))))
    seg[0].append(_text(600, 44, "血细胞", 11.5, "#b03070"))
    for order in range(4):
        seg[0].append(_dot(_path_of([(50 + order * 130, 124), (170 + order * 130, 124)]),
                           dur=2.6, begin=round(order * 0.5, 2), r=6, color="#4aa3e0"))

    # 第 2 段：组织液——组织细胞生活的环境
    seg[1].append('<rect x="24" y="176" width="440" height="150" rx="16" fill="none" '
                  'stroke="#1f6ea8" stroke-width="3" stroke-dasharray="12 8"/>')
    seg[1].append(_tag(330, 160, "② 组织液：组织细胞生活的环境", "#1f6ea8"))
    for order in range(3):
        seg[1].append('<circle cx="%d" cy="250" r="30" fill="#fff5e0" stroke="#d9ab3c" '
                      'stroke-width="2.4"><animate attributeName="r" values="30;32;30" '
                      'dur="2.4s" begin="%gs" repeatCount="indefinite"/></circle>'
                      % (120 + order * 120, round(order * 0.4, 2)))
    seg[1].append(_text(86, 320, "组织细胞", 11.5, "#8a6a1f"))
    for order in range(4):
        seg[1].append(_dot(_path_of([(150 + order * 100, 140), (160 + order * 100, 216)]),
                           dur=2.4, begin=round(order * 0.5, 2), r=6, color="#e0518f"))
    for order in range(3):
        seg[1].append(_dot(_path_of([(120 + order * 120, 222), (120 + order * 120, 176)]),
                           dur=2.6, begin=round(0.4 + order * 0.6, 2), r=6, color="#4aa3e0"))

    # 第 3 段：淋巴——一部分组织液进入毛细淋巴管
    seg[2].append('<path d="M 500 190 q 60 34 0 70 q -60 34 0 66" fill="none" '
                  'stroke="#2f7d52" stroke-width="4" stroke-dasharray="10 7"/>')
    seg[2].append(_tag(330, 160, "③ 淋巴：一部分组织液进入毛细淋巴管", "#2f7d52"))
    for order in range(3):
        seg[2].append(_dot(_path_of([(470, 208 + order * 34), (520, 224 + order * 30)]),
                           dur=2.4, begin=round(order * 0.7, 2), r=6, color="#3f9e6b"))
    seg[2].append(_text(330, 340, "单向回流，最终汇入血浆", 12, "#2f7d52"))

    # 第 4 段：三者关系（血浆 -> 组织液 -> 淋巴 -> 血浆）
    seg[3].append(_tag(330, 160, "④ 三者关系：血浆 -> 组织液 -> 淋巴 -> 血浆", "#8a5fb0"))
    seg[3].append(_arrow(300, 138, 300, 174, True, "#8a5fb0"))
    seg[3].append(_arrow(200, 176, 200, 140, True, "#e0518f"))
    seg[3].append(_arrow(452, 244, 494, 238, True, "#3f9e6b"))
    seg[3].append(_dot(_path_of([(548, 200), (620, 120), (300, 98)]), dur=4.0, r=7,
                       color="#8a5fb0"))
    seg[3].append(_text(330, 340, "内环境 = 血浆 + 组织液 + 淋巴，物质交换靠它们完成", 11.5,
                        "#3a4652"))

    # 只有本段亮着并脉动，别的段淡出
    for order, body in enumerate(seg):
        on = order == active
        parts.append('<g opacity="%g">%s%s</g>'
                     % (1 if on else 0.24, _pulse(1.8, 0.6) if on else "", "".join(body)))
    return (_head("内环境的组成关系（仿真：物质在血浆/组织液/淋巴之间不断交换）")
            + "".join(parts) + _caption(steps, index, H - 24) + "</svg>")



def _sim_humoral_immunity(steps, index):
    """体液免疫全过程：抗原进来 -> 吞噬细胞处理呈递 -> T 细胞（淋巴因子）-> B 细胞增殖分化
    -> 浆细胞分泌抗体 -> 抗体与抗原结合成沉淀 -> 记忆细胞留下。画面按段演：只有 index 这一段
    亮着并脉动；一直在场的那几种免疫细胞轮廓是公共背景。"""
    import math
    seg = [[], [], [], []]
    active = min(3, index * 3 // max(1, len(steps) - 1))
    parts = ['<rect width="%d" height="%d" fill="#f7f9fc"/>' % (W, H)]
    # 公共背景：一直在场的那几种细胞（轮廓 + 名字）
    parts.append('<circle cx="150" cy="180" r="48" fill="#e6e0f5" stroke="#8a7fb0" '
                 'stroke-width="2.6"/>')
    parts.append(_text(150, 246, "吞噬细胞", 11.5, "#5b4f80"))
    parts.append('<circle cx="288" cy="120" r="32" fill="#cfe6fb" stroke="#3f6ea8" '
                 'stroke-width="2.4"/>')
    parts.append(_text(288, 168, "T 细胞", 11.5, "#1f6ea8"))
    parts.append('<circle cx="392" cy="222" r="34" fill="#9fd3a8" stroke="#3f9e6b" '
                 'stroke-width="2.6"/>')
    parts.append(_text(392, 272, "B 细胞", 11.5, "#2f7d52"))
    for order in range(3):
        parts.append('<circle cx="%d" cy="%d" r="20" fill="#f6d488" stroke="#d9ab3c" '
                     'stroke-width="2.2"/>' % (478 + order * 40, 152 - order * 12))
    parts.append(_text(518, 188, "浆细胞", 11.5, "#a3721a"))

    # 第 1 段：抗原进来，被吞噬细胞处理并呈递
    seg[0].append('<circle cx="150" cy="180" r="56" fill="none" stroke="#e08a2b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    seg[0].append('<circle cx="288" cy="120" r="40" fill="none" stroke="#e08a2b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    for order in range(3):
        seg[0].append('<g>%s<circle r="11" fill="#c0392b" stroke="#8a2b1f" '
                      'stroke-width="1.6"/>%s</g>'
                      % (_motion(_path_of([(20, 300), (120, 214)]), dur=4.0,
                                 begin=round(order * 1.1, 2)),
                         "".join('<line x1="0" y1="0" x2="%g" y2="%g" stroke="#8a2b1f" '
                                 'stroke-width="2"/>'
                                 % (14 * math.cos(a * 3.14159 / 180),
                                    -14 * math.sin(a * 3.14159 / 180))
                                 for a in range(0, 360, 60))))
    seg[0].append(_tag(56, 330, "抗原", "#c0392b"))
    seg[0].append('<circle cx="150" cy="180" r="16" fill="#c0392b">%s</circle>'
                  % _fade("0;0;1;1;0", 4.0, "0;0.25;0.4;0.9;1"))
    for order in range(3):
        seg[0].append(_dot(_path_of([(190, 158), (258, 134)]), dur=1.8,
                           begin=round(order * 0.5, 2), r=6, color="#8a7fb0"))
        seg[0].append(_dot(_path_of([(300, 148), (372, 200)]), dur=1.8,
                           begin=round(0.3 + order * 0.5, 2), r=6, color="#4aa3e0"))
    seg[0].append(_tag(330, 336, "① 抗原被吞噬细胞处理，再呈递给 T 细胞、B 细胞", "#5b4f80"))

    # 第 2 段：B 细胞在淋巴因子作用下增殖分化
    seg[1].append('<circle cx="392" cy="222" r="42" fill="none" stroke="#2f9e6b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    for order in range(3):
        seg[1].append(_dot(_path_of([(304, 148), (370, 198)]), dur=1.8,
                           begin=round(order * 0.5, 2), r=6, color="#4aa3e0"))
    for order, (cx, cy) in enumerate(((316, 278), (392, 296), (452, 258))):
        seg[1].append('<circle cx="%d" cy="%d" r="18" fill="#c8e6cf" stroke="#3f9e6b" '
                      'stroke-width="2">%s</circle>'
                      % (cx, cy, _fade("0;0;1;1", 4.0,
                                       "0;%g;%g;1" % (0.15 + order * 0.15,
                                                      0.25 + order * 0.15))))
        seg[1].append('<circle cx="%d" cy="%d" r="6" fill="#2f7d52"/>' % (cx, cy))
    seg[1].append(_tag(330, 336, "② B 细胞受刺激后增殖分化（淋巴因子起作用）", "#2f7d52"))

    # 第 3 段：形成浆细胞和记忆细胞
    for order in range(3):
        seg[2].append('<circle cx="%d" cy="%d" r="28" fill="none" stroke="#e08a2b" '
                      'stroke-width="3" stroke-dasharray="10 7"/>'
                      % (478 + order * 40, 152 - order * 12))
    seg[2].append(_arrow(392, 256, 470, 182, True, "#a3721a"))
    seg[2].append(_arrow(372, 250, 300, 296, True, "#8a5fb0"))
    seg[2].append('<circle cx="170" cy="300" r="26" fill="#f0e8fa" stroke="#8a5fb0" '
                  'stroke-width="2.4">%s</circle>'
                  % _fade("0;0;1;1;1", 6.0, "0;0.55;0.7;0.95;1"))
    seg[2].append(_tag(170, 344, "记忆细胞", "#6a4fb0"))
    seg[2].append(_tag(452, 344, "③ 形成浆细胞和记忆细胞", "#a3721a"))

    # 第 4 段：浆细胞分泌抗体，抗体与抗原结合成沉淀
    for order in range(4):
        seg[3].append('<g>%s<path d="M -11 -8 L 0 6 L 11 -8" fill="none" stroke="#e08a2b" '
                      'stroke-width="4" stroke-linecap="round"/></g>'
                      % _motion(_path_of([(530, 170), (612, 250)]), dur=2.6,
                                begin=round(order * 0.6, 2)))
    seg[3].append(_tag(600, 210, "抗体", "#a35a12"))
    for order in range(4):
        seg[3].append('<circle cx="%d" cy="%d" r="12" fill="#f3d6d6" stroke="#c0392b" '
                      'stroke-width="2.2">%s</circle>'
                      % (556 + (order % 2) * 34, 268 + (order // 2) * 32,
                         _pulse(2.4, 0.6, round(order * 0.3, 2))))
    seg[3].append('<ellipse cx="573" cy="284" rx="48" ry="46" fill="none" stroke="#c0392b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    seg[3].append(_tag(560, 340, "结合成沉淀/细胞集团被清除", "#c0392b"))

    # 只有本段亮着并脉动，别的段淡出
    for order, body in enumerate(seg):
        on = order == active
        parts.append('<g opacity="%g">%s%s</g>'
                     % (1 if on else 0.24, _pulse(1.8, 0.6) if on else "", "".join(body)))
    return (_head("体液免疫全过程（仿真：抗原进来 -> 吞噬 -> T/B 细胞 -> 抗体 -> 沉淀）")
            + "".join(parts) + _caption(steps, index, H - 24) + "</svg>")



def _sim_cellular_immunity(steps, index):
    """细胞免疫：抗原呈递 -> 辅助 T 细胞（细胞因子）-> 细胞毒性 T 细胞增殖 -> 找到靶细胞
    -> 使靶细胞裂解死亡 -> 记忆 T 细胞留下、抗体清除释放出的抗原。画面按段演：只有 index
    这一段亮着并脉动；一直在场的那几种免疫细胞轮廓是公共背景。"""
    import math
    seg = [[], [], [], []]
    active = min(3, index * 3 // max(1, len(steps) - 1))
    parts = ['<rect width="%d" height="%d" fill="#f7f9fc"/>' % (W, H)]
    # 公共背景：一直在场的那几种细胞（轮廓 + 名字）
    parts.append('<circle cx="132" cy="84" r="40" fill="#e6e0f5" stroke="#8a7fb0" '
                 'stroke-width="2.4"/>')
    parts.append(_text(132, 140, "抗原呈递细胞", 11.5, "#5b4f80"))
    parts.append('<circle cx="268" cy="84" r="30" fill="#cfe6fb" stroke="#3f6ea8" '
                 'stroke-width="2.4"/>')
    parts.append(_text(268, 140, "辅助 T 细胞", 11.5, "#1f6ea8"))
    for order in range(3):
        parts.append('<circle cx="%d" cy="216" r="26" fill="#9fd3a8" stroke="#3f9e6b" '
                     'stroke-width="2.4"/>' % (300 + order * 60))
    parts.append(_text(360, 258, "细胞毒性 T 细胞", 11.5, "#2f7d52"))
    parts.append('<circle cx="560" cy="236" r="48" fill="#fdecec" stroke="#c96a6a" '
                 'stroke-width="2.6"/>')
    parts.append(_text(560, 300, "靶细胞", 11.5, "#a33f3f"))

    # 第 1 段：抗原呈递细胞把抗原呈递给 T 细胞
    seg[0].append('<circle cx="132" cy="84" r="48" fill="none" stroke="#e08a2b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    seg[0].append('<circle cx="268" cy="84" r="38" fill="none" stroke="#e08a2b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    for order in range(4):
        seg[0].append('<circle cx="%d" cy="%d" r="7" fill="#c0392b">%s</circle>'
                      % (114 + (order % 2) * 32, 68 + (order // 2) * 32,
                         _pulse(2.0, 0.5, round(order * 0.3, 2))))
    for order in range(3):
        seg[0].append(_dot(_path_of([(172, 96), (238, 104)]), dur=1.8,
                           begin=round(order * 0.5, 2), r=6, color="#4aa3e0"))
    seg[0].append(_tag(330, 152, "① 抗原呈递细胞把抗原呈递给 T 细胞", "#5b4f80"))

    # 第 2 段：T 细胞增殖分化成细胞毒性 T 细胞和记忆 T 细胞
    seg[1].append('<rect x="262" y="178" width="196" height="76" rx="38" fill="none" '
                  'stroke="#2f9e6b" stroke-width="3" stroke-dasharray="10 7"/>')
    seg[1].append('<circle cx="268" cy="116" r="40" fill="none" stroke="#2f9e6b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    for order in range(3):
        seg[1].append(_dot(_path_of([(272, 116), (330, 180)]), dur=1.8,
                           begin=round(0.4 + order * 0.5, 2), r=6, color="#8a6cd0"))
    seg[1].append('<circle cx="170" cy="300" r="26" fill="#f0e8fa" stroke="#8a5fb0" '
                  'stroke-width="2.4">%s</circle>'
                  % _fade("0;0;1;1;1", 5.0, "0;0.6;0.75;0.95;1"))
    seg[1].append(_arrow(300, 244, 200, 286, True, "#8a5fb0"))
    seg[1].append(_tag(170, 344, "记忆 T 细胞（同样抗原再来时迅速反应）", "#6a4fb0"))
    seg[1].append(_tag(430, 344, "② T 细胞增殖分化", "#2f7d52"))

    # 第 3 段：细胞毒性 T 细胞识别并接触靶细胞
    seg[2].append('<circle cx="420" cy="216" r="34" fill="none" stroke="#2f9e6b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    seg[2].append('<circle cx="560" cy="236" r="56" fill="none" stroke="#c0392b" '
                  'stroke-width="3" stroke-dasharray="10 7"/>')
    for order in range(4):
        seg[2].append('<circle cx="%d" cy="%d" r="8" fill="#c0392b">%s</circle>'
                      % (542 + (order % 2) * 30, 216 + (order // 2) * 34,
                         _pulse(2.0, 0.5, round(order * 0.3, 2))))
    seg[2].append(_arrow(452, 222, 508, 232, True, "#c0392b"))
    for order in range(3):
        seg[2].append(_dot(_path_of([(430, 216), (516, 232)]), dur=1.6,
                           begin=round(order * 0.4, 2), r=6, color="#3f9e6b"))
    seg[2].append(_tag(560, 172, "被病毒感染的靶细胞", "#a33f3f"))
    seg[2].append(_tag(330, 152, "③ 细胞毒性 T 细胞识别并接触靶细胞", "#2f7d52"))

    # 第 4 段：靶细胞裂解死亡，释放出的抗原再由抗体清除
    seg[3].append('<ellipse cx="560" cy="236" rx="54" ry="44" fill="none" stroke="#c0392b" '
                  'stroke-width="3" stroke-dasharray="14 12"/>')
    for order in range(5):
        angle = (120 + order * 30) * 3.14159 / 180
        seg[3].append('<g>%s<circle r="9" fill="#f3d6d6" stroke="#c96a6a" '
                      'stroke-width="1.6">%s</circle></g>'
                      % (_motion(_path_of([(560, 236),
                                           (560 + 110 * math.cos(angle),
                                            236 + 90 * math.sin(angle))]),
                                 dur=2.4, begin=round(order * 0.4, 2)),
                         _fade("0;0;1;1;0", 2.4, "0;0.1;0.3;0.8;1")))
    for order in range(5):
        seg[3].append('<g>%s<path d="M -12 -9 L 0 7 L 12 -9" fill="none" stroke="#e08a2b" '
                      'stroke-width="4" stroke-linecap="round">%s</path></g>'
                      % (_motion(_path_of([(640, 320), (560, 260)]), dur=2.6,
                                 begin=round(order * 0.5, 2)),
                         _fade("0;0;1;0", 2.6, "0;0.15;0.35;1")))
    seg[3].append(_tag(600, 152, "抗体清除抗原", "#a35a12"))
    seg[3].append(_tag(330, 152, "④ 靶细胞裂解死亡，抗原再由抗体清除", "#2f7d52"))

    # 只有本段亮着并脉动，别的段淡出
    for order, body in enumerate(seg):
        on = order == active
        parts.append('<g opacity="%g">%s%s</g>'
                     % (1 if on else 0.24, _pulse(1.8, 0.6) if on else "", "".join(body)))
    return (_head("细胞免疫全过程（仿真：呈递 -> 细胞因子 -> 效应 T 细胞 -> 靶细胞裂解）")
            + "".join(parts) + _caption(steps, index, H - 24) + "</svg>")



def _spotlight(svg, key, index):
    """聚光灯：只让当前这一段对应的区域亮着，其它区域蒙一层白纱，该区域加脉动虚线圈。

    这是让每一步画面都不一样的通用做法，不必去改各场景函数里的元素分组。
    """
    spots = FOCUS.get(key)
    if not spots:
        return svg
    x, y, width, height = spots[min(index, len(spots) - 1)]
    bottom = 338                                   # 图注在下面，别把图注也蒙上
    bands = [(0, 0, W, y), (0, y + height, W, max(0, bottom - (y + height))),
             (0, y, x, height), (x + width, y, max(0, W - (x + width)), height)]
    parts = []
    for bx, by, bw, bh in bands:
        if bw > 0 and bh > 0:
            parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#ffffff" '
                         'opacity="0.74"/>' % (bx, by, bw, bh))
    parts.append('<rect x="%g" y="%g" width="%g" height="%g" rx="14" fill="none" '
                 'stroke="#e08a2b" stroke-width="3" stroke-dasharray="9 6">%s</rect>'
                 % (x, y, width, height, _pulse(1.6, 0.35)))
    return svg.replace("</svg>", "".join(parts) + "</svg>")


#: 每个仿真场景每一段该看哪儿（x, y, 宽, 高）：聚光灯用它
FOCUS = {
    'light_reaction': [(50, 30, 440, 210), (380, 140, 270, 140), (430, 200, 220, 140)],
    'aerobic_respiration': [(10, 80, 190, 225), (280, 110, 300, 210), (400, 210, 250, 130)],
    'calvin_cycle': [(30, 120, 200, 140), (240, 120, 340, 190), (500, 220, 160, 130)],
    'atp_adp': [(30, 70, 300, 90), (260, 140, 200, 120), (420, 70, 230, 90)],
    'dna_replication': [(20, 90, 250, 200), (280, 90, 100, 200), (388, 90, 260, 200)],
    'transcription': [(10, 110, 320, 140), (250, 100, 190, 150), (30, 190, 500, 120)],
    'translation': [(40, 195, 590, 90), (240, 140, 200, 140), (50, 50, 570, 130)],
    'pcr': [(40, 45, 590, 100), (140, 190, 380, 130), (140, 190, 380, 130)],
    'secretory_path': [(150, 110, 230, 100), (400, 110, 130, 120), (520, 200, 140, 140)],
    'dehydration': [(40, 70, 220, 150), (400, 70, 220, 150), (40, 240, 570, 90)],
    'protein_structure': [(18, 50, 150, 245), (176, 50, 150, 245), (334, 50, 150, 245), (492, 50, 150, 245)],
    'membrane_model': [(0, 130, 660, 120), (110, 120, 240, 130), (60, 90, 540, 200)],
    'plasmolysis': [(85, 55, 490, 290), (40, 30, 580, 310), (40, 30, 580, 310)],
    'active_transport': [(10, 140, 640, 60), (240, 100, 160, 150), (110, 230, 240, 100)],
    'enzyme_barrier': [(50, 70, 580, 230), (50, 70, 580, 230)],
    'enzyme_conditions': [(40, 60, 280, 240), (340, 60, 280, 240)],
    'mitosis_curve': [(30, 40, 610, 120), (40, 160, 600, 150), (40, 160, 600, 150)],
    'cell_differentiation': [(280, 30, 160, 90), (40, 150, 590, 130), (40, 150, 590, 130)],
    'nuclear_transfer': [(10, 110, 240, 180), (290, 50, 200, 250), (460, 110, 130, 200), (520, 200, 130, 130), (240, 320, 440, 60)],
    'internal_environment': [(10, 55, 640, 85), (10, 165, 460, 160), (480, 180, 170, 150)],
    'humoral_immunity': [(90, 120, 130, 130), (230, 80, 160, 160), (430, 80, 160, 130), (490, 265, 180, 120), (240, 285, 110, 100)],
    'cellular_immunity': [(80, 30, 240, 120), (260, 170, 190, 90), (490, 170, 150, 130), (240, 295, 140, 90)],
    'mitosis': [(0, 60, 660, 150), (0, 60, 660, 150), (0, 60, 660, 150), (0, 60, 660, 150), (0, 60, 660, 150)],
    'meiosis': [(0, 60, 660, 150), (0, 60, 660, 150), (0, 60, 660, 150), (0, 60, 660, 150)],
}


#: 有专门仿真场景的过程 -> 场景函数（没有的就用通用分步画法）
SIMS = {
    "cellular_immunity": _sim_cellular_immunity,
    "internal_environment": _sim_internal_environment,
    "humoral_immunity": _sim_humoral_immunity,
    "atp_adp": _sim_atp_adp,
    "cell_differentiation": _sim_differentiation,
    "nuclear_transfer": _sim_nuclear_transfer,
    "light_reaction": _sim_photosynthesis,
    "aerobic_respiration": _sim_respiration,
    "dna_replication": _sim_replication,
    "transcription": _sim_transcription,
    "translation": _sim_translation,
    "osmosis": _sim_osmosis,
    "action_potential": _sim_action_potential,
    "potential_curve": _sim_action_potential,
    "pcr": _sim_pcr,
    "secretory_path": _sim_secretory,
    # 批次 2（必修1 其余）
    "dehydration": _sim_dehydration,
    "protein_structure": _sim_protein_structure,
    "membrane_model": _sim_membrane_model,
    "plasmolysis": _sim_plasmolysis,
    "active_transport": _sim_active_transport,
    "enzyme_barrier": _sim_enzyme_barrier,
    "enzyme_conditions": _sim_enzyme_conditions,
    "atp_adp": _sim_atp_adp,
    "calvin_cycle": _sim_calvin_cycle,
    "mitosis_curve": _sim_mitosis_curve,
    "cell_differentiation": _sim_differentiation,
}


def figure(key, index=0):
    """按当前阶段画一张图（SVG 字符串）。"""
    row = process(key)
    steps = row["steps"]
    index = max(0, min(int(index), len(steps) - 1))
    title = "%s（%s）" % (row["name"], row["chapter"])
    kind = row["kind"]
    curve_name = None
    for item in PROCESSES:
        if item[0] == row["key"]:
            curve_name = item[4]
    if kind == "curve":
        return _curve_figure(key, steps, index, curve_name)
    if row["key"] in ("mitosis", "meiosis"):
        return _spotlight(_division_scene(steps, index,
                                          meiosis=row["key"] == "meiosis"),
                          row["key"], index)
    if row["key"] in SIMS:
        return _spotlight(SIMS[row["key"]](steps, index), row["key"], index)
    if kind == "cycle":
        return _cycle(steps, index) + _caption(steps, index) + "</svg>"
    if kind == "membrane":
        return _membrane(steps, index, title) + _caption(steps, index) + "</svg>"
    if kind == "grid":
        body = _mono_grid(steps, index) if curve_name == "mono" else _di_grid(steps, index)
        return body + _caption(steps, index, H - 24) + "</svg>"
    if kind == "tree":
        return _tree(steps, index) + _caption(steps, index) + "</svg>"
    return _chain(steps, index) + _caption(steps, index) + "</svg>"
