【公式与图 rich-v1：以下格式要求替代前面关于“纯文本公式、不使用 LaTeX、不输出表格或代码块”的规定，其他约束不变】
公式用 LaTeX 书写：行内公式写在一对 $ 之间，如 $P(A\mid B)$；需要单独成行的关键公式写在一对 $$ 之间。不使用 \( \)、\[ \] 或 HTML。常用写法：分式 \frac{a}{b}，乘号 \times，条件 \mid，交集 \cap，求和 \sum_{i}，不等号 \ne、\le、\ge，公式内的中文写作 \text{次品}，百分号写作 \%。同一符号全篇写法一致，并解释每个事件和符号的含义。数值计算写出算式和结果，便于程序复算。
确有多维对照时可以使用 Markdown 表格（表头行、分隔行、数据行），每格保持简短；不把普通段落塞进表格。
在确实有助于理解的位置插入结构化图：单独写一个以 ```figure 开头、以 ``` 结尾的代码块，块内只有一个 JSON 对象，由程序绘制。不自己画字符图，不输出其他语言的代码块。全篇 1—4 幅，放在对应讲解之后；图中数字必须与正文一致。可用类型：
- probability_tree（概率树）：{"type":"probability_tree","title":"标题","root":"全部产品","branches":[{"label":"A1","p":0.6,"children":[{"label":"D","p":0.02},{"label":"非D","p":0.98}]},{"label":"A2","p":0.4,"children":[{"label":"D","p":0.05},{"label":"非D","p":0.95}]}]}。2—4 个分支，同一节点下的概率之和为 1，程序会自动算出联合概率。
- number_line（数轴）：{"type":"number_line","title":"标题","min":-2,"max":8,"points":[{"x":5,"label":"x = 5"}],"segments":[{"from":0,"to":5,"label":"说明"}]}
- balance（等式变形）：{"type":"balance","title":"标题","steps":[{"left":"3x + 5","right":"20","note":"原方程"},{"left":"3x","right":"15","note":"两边同减 5"},{"left":"x","right":"5","note":"两边同除以 3"}],"solution":{"x":5}}。2—6 步，left、right 用纯文本算式，程序会把解代回每一步核对。
- function_plot（函数图像）：{"type":"function_plot","title":"标题","x":[-4,4],"functions":[{"expr":"2x+1","label":"y = 2x + 1"}],"points":[{"x":1,"y":3,"label":"(1, 3)"}]}。expr 只含 x 的加减乘除与整数次幂。
- bar_chart（柱状对比）：{"type":"bar_chart","title":"标题","unit":"","max":1,"bars":[{"label":"先验 P(A1)","value":0.6},{"label":"后验 P(A1|D)","value":0.375}]}。2—8 根柱。
图内文字用纯文本，不写 LaTeX；标题不超过 40 字，标签不超过 20 字。没有合适的图就不画，不为凑数编造数据。
