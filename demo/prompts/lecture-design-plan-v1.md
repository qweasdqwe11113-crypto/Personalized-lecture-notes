【本次任务：课堂设计 lesson-design-v1】
先做设计，不写详细讲稿。只输出一个 JSON 对象，不输出 Markdown 或其他文字。

按下面的顺序想清楚再写（不输出思考过程）：
1. 这节课最要紧的 1—2 件事是什么（重点）？学生最容易卡在哪里（难点）？哪些内容只提一句或留作课后阅读（not_today）？重点决定时间：重点环节拿最多的分钟；前置知识只做前测或一两句回顾，不重新推导。
2. 时间是硬约束。授课时长是 teaching_options.duration 分钟，各环节 minutes 之和必须等于它。一道完整例题连板演带提问至少 4 分钟，一道当堂练习连讲评至少 3 分钟。装不下就减内容，不要压缩时间。
3. 例题恰好 teaching_options.examples 道（role 为 example），当堂练习恰好 teaching_options.exercises 道（role 为 practice）。练习分层：level 为 basic（全体必做）或 advanced（学有余力选做）。课后作业 2—4 道（role 为 homework），同样分层；作业题不放进环节的 items，程序会单独列出。需要前测时加 1 道 role 为 warmup 的小题。
4. 每条学习目标都要能被某道练习或作业检验，把题目编号写进 checked_by；写进去的题必须真的考到这条目标。
5. 例题之间要有梯度或对比，不要两道题考同一件事。练习不照抄例题，换情境或换问法。所有题目沿用 notation 里的符号。题目里的数字自己算一遍，答案和每一步都要对得上。
6. 授课类型 teaching_options.lesson_type：new 新授、practice 巩固、correction 纠错、review 复习。巩固、纠错、复习课不重讲定义，时间主要给例题和练习。
7. teaching_options.requirements 是教师对这节课的要求，优先满足；与时长冲突时在 learner 里说明取舍。context.reference_examples 存在时，例题采用其中给定的数据。

JSON 格式（字段名固定，不增加字段）：
{"title":"课题","focus":{"key_points":["重点"],"difficulties":["难点"],"not_today":["本课不展开的内容及去向"]},"learner":"学情与对策","plan_links":[{"knowledge_id":1,"segment":"S2","how":"这条讲解安排落在哪里、怎么做"}],"notation":[{"symbol":"$X$","meaning":"含义"}],"objectives":[{"text":"学生能做到的事","checked_by":["P1"]}],"segments":[{"id":"S1","kind":"warmup","title":"环节名","minutes":3,"focus":false,"knowledge_ids":[1],"screen":"屏幕上放什么","board":["板书内容"],"teacher":["教师做什么"],"students":"学生做什么","questions":[{"ask":"问题","expect":"预期回答","wrong":"常见错答","follow_up":"追问","level":"basic"}],"items":["W1"],"if_short":"时间不够时怎么办","sources":["M1"]}],"items":[{"id":"E1","role":"example","level":"basic","knowledge_id":1,"title":"题目名","stem":"完整题干","steps":[{"do":"这一步的式子或操作","why":"依据或提醒"}],"answer":"最终答案","check":"检验","mistake":"典型错误","sources":["M1"]}],"board_plan":{"main":["主板书"],"side":["副板书"]},"figures":[{"segment":"S2","spec":{"type":"bar_chart"}}],"summary":["小结要点"]}

字段说明：
- title：最多 40 字。focus.key_points 1—2 条，difficulties 0—2 条，not_today 0—4 条，每条一句话。
- learner：学情与对策，1—3 句，写“依据什么，所以这节课怎么调整”；有待核对的依据在这里提醒一次。没有学生信息时写空串。
- plan_links：personalization_plan 有几条，这里就有几项。knowledge_id 照抄讲解安排里的数字，segment 写落实它的环节编号，how 用一句话说清怎么落实（写教学动作，如“用例1 的错解对比”）。没有讲解安排时为空数组。
- notation：本课用到的符号 0—8 个。objectives：2—4 条，用学生能做到的行为来写（“能写出……并代入计算”）。
- segments：按课堂时间顺序 4—8 个。kind 取 warmup（前测与回顾）、intro（导入）、concept（新授）、example（例题）、practice（当堂练习）、summary（小结）、homework（布置作业）。focus 为 true 表示重点环节，全课 1—3 个。title 只写这个环节的内容（如“泊松分布的定义”），不带“例1”“当堂练习”这类环节类型或题号，程序会加上。
  - screen：这个环节屏幕上放什么，一句话。board：写上黑板的内容，0—4 行。
  - teacher：教师在这个环节依次做的事，2—5 条，每条只写一个动作，不超过 30 个字；怎么讲的细节留给后面的详案。students：学生在做什么，一句话。
  - questions：0—3 个关键提问。ask 是问题，expect 是预期回答，wrong 是常见错答，follow_up 是追问，level 取 basic 或 advanced。全课关键提问 3—6 个，放在最需要学生想的地方。
  - items：这个环节用到的题目编号。每道例题、练习、前测题恰好属于一个环节。
  - if_short：时间不够时这个环节怎么压缩；不能省就写“不能省”。
- 在 learner、teacher、screen、if_short 等文字里提到题目或环节时，直接写编号（E1、P2、S3），程序会换成“例1”“练2”“第 3 环节”。
- items：全课的题目只在这里写一次。id 用 E1、P1、W1、H1 这样的编号。stem 是完整题干，条件充分、没有歧义。例题的 steps 写 2—6 步，每步 do 是式子或操作，why 是依据或提醒；练习和作业可以不写 steps，但必须有 answer。check 写检验办法或结果，mistake 写这道题学生最容易犯的错，都可以为空串。title 是 4—12 字的题目名。
- board_plan：main 是整节课留在黑板上的骨架，3—8 行；side 是演算区的安排，0—4 条。
- summary：2—4 条小结要点，用学生的口吻。

公式写法：公式用 LaTeX，行内写在一对 $ 之间，如 $P(A\mid B)$。分式 \frac{a}{b}，乘号 \times，不等号 \neq、\le、\ge，求和 \sum_{i}，公式里的中文写 \text{次品}，百分号 \%。输出是 JSON，字符串里的每个反斜杠要写成两个，例如 "$\\frac{1}{2}$"。数值计算写出算式和结果，便于程序复算。

图：figures 放 0—3 幅确实有助于理解的图，由程序按 spec 里的数据绘制，放在 segment 指定的环节。图中数字必须与题目一致，图内文字用纯文本不写 LaTeX，标题最多 40 字、标签最多 20 字。没有合适的图就给空数组，不为凑数编造数据。spec 的可用类型：
- 概率树：{"type":"probability_tree","title":"标题","root":"全部产品","branches":[{"label":"A1","p":0.6,"children":[{"label":"D","p":0.02},{"label":"非D","p":0.98}]},{"label":"A2","p":0.4,"children":[{"label":"D","p":0.05},{"label":"非D","p":0.95}]}]}，2—4 个分支，同一节点下概率之和为 1。
- 数轴：{"type":"number_line","title":"标题","min":-2,"max":8,"points":[{"x":5,"label":"x = 5"}],"segments":[{"from":0,"to":5,"label":"说明"}]}
- 等式变形：{"type":"balance","title":"标题","steps":[{"left":"3x + 5","right":"20","note":"原方程"},{"left":"3x","right":"15","note":"两边同减 5"},{"left":"x","right":"5","note":"两边同除以 3"}],"solution":{"x":5}}，2—6 步。
- 函数图像：{"type":"function_plot","title":"标题","x":[-4,4],"functions":[{"expr":"2x+1","label":"y = 2x + 1"}],"points":[{"x":1,"y":3,"label":"(1, 3)"}]}，expr 可用四则运算、乘方和 exp、ln、sqrt、abs、sin、cos、tan。
- 柱状对比：{"type":"bar_chart","title":"标题","unit":"","max":1,"bars":[{"label":"先验","value":0.6},{"label":"后验","value":0.375}]}，2—8 根柱。
