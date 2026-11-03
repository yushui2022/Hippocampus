# Live2D 拆层与绑定计划

正面站姿动漫少女：棕色齐肩短发、粉色连帽卫衣、黑色短裤、黑色过膝袜与粉色运动鞋。适合按头身分离、眼口表情、头发与帽绳物理、手臂和腿部小幅动作进行 Live2D 拆层。

## 图层
- `hair_back`：后发和头后轮廓，支撑头部旋转时后发遮挡与摆动。；顺序 0；参数 `ParamHairBack`；补画：True
- `hood_back`：卫衣帽子后片与肩后结构，身体倾斜时防止露底。；顺序 1；参数 `ParamBodyAngleZ`；补画：True
- `neck`：颈部皮肤，连接头部与躯干并随头部转动。；顺序 2；参数 `ParamAngleZ`；补画：True
- `torso_under`：卫衣下躯干和骨盆补画层，防止身体弯曲漏底。；顺序 3；参数 `ParamBodyAngleX`；补画：True
- `leg_skin_character_right`：角色右腿裸腿皮肤底层，补画被长袜、短裤和鞋遮挡部分。；顺序 4；参数 `ParamLegR`；补画：True
- `leg_skin_character_left`：角色左腿裸腿皮肤底层，补画被长袜、短裤和鞋遮挡部分。；顺序 5；参数 `ParamLegL`；补画：True
- `stocking_character_right`：角色右腿黑色过膝袜，随腿部小幅摆动。；顺序 6；参数 `ParamLegR`；补画：False
- `stocking_character_left`：角色左腿黑色过膝袜，随腿部小幅摆动。；顺序 7；参数 `ParamLegL`；补画：False
- `shorts`：黑色短裤主体，遮挡大腿根，需补画上衣下摆内侧。；顺序 8；参数 `ParamBodyAngleX`；补画：True
- `shoe_character_right`：角色右脚运动鞋，随脚部微小移动。；顺序 9；参数 `ParamFootR`；补画：False
- `shoe_character_left`：角色左脚运动鞋，随脚部微小移动。；顺序 10；参数 `ParamFootL`；补画：False
- `hoodie_body`：粉色卫衣躯干主体，身体呼吸与倾斜主变形层。；顺序 11；参数 `ParamBodyAngleX`；补画：True
- `hoodie_collar`：卫衣领口前片，覆盖颈部连接处。；顺序 12；参数 `ParamBodyAngleZ`；补画：False
- `hoodie_pocket`：卫衣前袋，随躯干轻微形变。；顺序 13；参数 `ParamBodyAngleX`；补画：False
- `hoodie_drawstring_viewer_left`：画面左侧帽绳，用于轻微物理摆动。；顺序 14；参数 `ParamHoodStringL`；补画：False
- `hoodie_drawstring_viewer_right`：画面右侧帽绳，用于轻微物理摆动。；顺序 15；参数 `ParamHoodStringR`；补画：False
- `sleeve_character_right`：角色右袖，手臂摆动主层，需补画被袖子遮挡的手臂。；顺序 16；参数 `ParamArmRA`；补画：True
- `sleeve_character_left`：角色左袖，手臂摆动主层，需补画被袖子遮挡的手臂。；顺序 17；参数 `ParamArmLA`；补画：True
- `hand_character_right`：角色右手，手臂末端跟随，需补画袖口内手腕。；顺序 18；参数 `ParamHandR`；补画：True
- `hand_character_left`：角色左手，手臂末端跟随，需补画袖口内手腕。；顺序 19；参数 `ParamHandL`；补画：True
- `face_base`：面部皮肤底色，承载眼口鼻，并需补画刘海和侧发遮挡区。；顺序 20；参数 `ParamAngleZ`；补画：True
- `eye_white_viewer_left`：画面左眼眼白，随眼睑开合。；顺序 21；参数 `ParamEyeLOpen`；补画：True
- `eye_white_viewer_right`：画面右眼眼白，随眼睑开合。；顺序 22；参数 `ParamEyeROpen`；补画：True
- `iris_viewer_left`：画面左眼虹膜和瞳孔，用于视线移动。；顺序 23；参数 `ParamEyeBallX`；补画：False
- `iris_viewer_right`：画面右眼虹膜和瞳孔，用于视线移动。；顺序 24；参数 `ParamEyeBallX`；补画：False
- `eyelash_upper_viewer_left`：画面左眼上睫毛与眼睑线，控制眨眼。；顺序 25；参数 `ParamEyeLOpen`；补画：False
- `eyelash_upper_viewer_right`：画面右眼上睫毛与眼睑线，控制眨眼。；顺序 26；参数 `ParamEyeROpen`；补画：False
- `eyelash_lower_viewer_left`：画面左眼下眼睑线，随闭眼微动。；顺序 27；参数 `ParamEyeLOpen`；补画：False
- `eyelash_lower_viewer_right`：画面右眼下眼睑线，随闭眼微动。；顺序 28；参数 `ParamEyeROpen`；补画：False
- `brow_viewer_left`：画面左眉毛，默认被刘海遮挡，需补画用于头部运动。；顺序 29；参数 `ParamBrowLY`；补画：True
- `brow_viewer_right`：画面右眉毛，默认被刘海遮挡，需补画用于头部运动。；顺序 30；参数 `ParamBrowRY`；补画：True
- `nose`：鼻头线与鼻影，面部小幅变形。；顺序 31；参数 `ParamAngleZ`；补画：False
- `mouth_inner`：口腔内部与舌头，张嘴时露出，需补画。；顺序 32；参数 `ParamMouthOpenY`；补画：True
- `mouth_line`：嘴部线条和唇形，控制微笑与开合。；顺序 33；参数 `ParamMouthForm`；补画：False
- `hair_side_viewer_left`：画面左侧发束，遮挡脸颊，随头部和物理摆动。；顺序 34；参数 `ParamHairSide`；补画：True
- `hair_side_viewer_right`：画面右侧发束，遮挡脸颊，随头部和物理摆动。；顺序 35；参数 `ParamHairSide`；补画：True
- `hair_bangs`：刘海主体，遮挡额头，头部转动和风效主层。；顺序 36；参数 `ParamHairFront`；补画：False

## 补画任务
- 补画被刘海遮挡的额头、眉弓和上眼睑上方皮肤，保持面部底色连续。
- 补画被侧发遮挡的脸颊、耳前和下颌边缘，供头部大角度转动。
- 补画颈部被下巴与卫衣领口遮挡的连接区域。
- 补画卫衣帽子后片及领口内侧，防止头部或身体转动露底。
- 补画卫衣袖下手臂和腋下轮廓，确保抬臂或摆动不穿帮。
- 补画袖口内手腕及手掌根部，手部摆动时保持连续。
- 补画卫衣下摆内侧、短裤上缘和骨盆区域。
- 补画短裤遮挡下的大腿根及双腿内侧。
- 补画过膝袜遮挡的腿部皮肤和袜口内侧，避免腿部摆动漏色。
- 补画鞋内脚踝和袜底被鞋遮挡部分。
- 补画眼白被上睫毛和眼睑遮挡区域，并分离虹膜高光以避免视线移动时高光漂移。
- 补画闭眼状态所需的下眼睑内侧与睫毛线内侧。
- 补画口腔内部、舌头和牙齿，供张嘴表情使用。
- 补画刘海下发际线、眉毛和耳前结构，供头部大角度转动。

## 参数
- `ParamAngleX` -30,30: face_base, neck, hair_bangs, hair_side_viewer_left, hair_side_viewer_right, hair_back, eye_white_viewer_left, eye_white_viewer_right, iris_viewer_left, iris_viewer_right, eyelash_upper_viewer_left, eyelash_upper_viewer_right, mouth_line
- `ParamAngleY` -30,30: face_base, neck, hair_bangs, hair_side_viewer_left, hair_side_viewer_right, eye_white_viewer_left, eye_white_viewer_right, iris_viewer_left, iris_viewer_right, mouth_line
- `ParamAngleZ` -30,30: face_base, neck, hair_bangs, hair_side_viewer_left, hair_side_viewer_right, hair_back, nose
- `ParamEyeLOpen` 0,1: eye_white_viewer_left, iris_viewer_left, eyelash_upper_viewer_left, eyelash_lower_viewer_left
- `ParamEyeROpen` 0,1: eye_white_viewer_right, iris_viewer_right, eyelash_upper_viewer_right, eyelash_lower_viewer_right
- `ParamEyeBallX` -1,1: iris_viewer_left, iris_viewer_right
- `ParamEyeBallY` -1,1: iris_viewer_left, iris_viewer_right
- `ParamMouthOpenY` 0,1: mouth_inner, mouth_line
- `ParamMouthForm` -1,1: mouth_line
- `ParamBrowLY` -1,1: brow_viewer_left
- `ParamBrowRY` -1,1: brow_viewer_right
- `ParamBodyAngleX` -10,10: hoodie_body, hoodie_pocket, hoodie_collar, torso_under, shorts, hood_back
- `ParamBodyAngleZ` -10,10: hoodie_body, hoodie_collar, hood_back, shorts, torso_under
- `ParamBreath` 0,1: hoodie_body, hoodie_pocket, hood_back, sleeve_character_right, sleeve_character_left
- `ParamHairFront` -1,1: hair_bangs
- `ParamHairSide` -1,1: hair_side_viewer_left, hair_side_viewer_right
- `ParamHairBack` -1,1: hair_back
- `ParamArmLA` -1,1: sleeve_character_left, hand_character_left
- `ParamArmRA` -1,1: sleeve_character_right, hand_character_right
- `ParamHandL` -1,1: hand_character_left
- `ParamHandR` -1,1: hand_character_right
- `ParamLegL` -10,10: leg_skin_character_left, stocking_character_left
- `ParamLegR` -10,10: leg_skin_character_right, stocking_character_right
- `ParamFootL` -10,10: shoe_character_left
- `ParamFootR` -10,10: shoe_character_right
- `ParamHoodStringL` -1,1: hoodie_drawstring_viewer_left
- `ParamHoodStringR` -1,1: hoodie_drawstring_viewer_right

## 物理
- `hair_back_sway`：ParamAngleX,ParamAngleZ,ParamBodyAngleX → ParamHairBack；图层：hair_back
- `hair_side_sway`：ParamAngleX,ParamAngleZ → ParamHairSide；图层：hair_side_viewer_left, hair_side_viewer_right
- `hair_front_sway`：ParamAngleX,ParamAngleY,ParamBodyAngleZ → ParamHairFront；图层：hair_bangs
- `drawstring_sway`：ParamBodyAngleX,ParamBodyAngleZ,ParamBreath → ParamHoodStringL,ParamHoodStringR；图层：hoodie_drawstring_viewer_left, hoodie_drawstring_viewer_right
- `hand_follow_arm`：ParamArmLA,ParamArmRA → ParamHandL,ParamHandR；图层：hand_character_left, hand_character_right

## 验收姿态
- 默认正面站立。
- 头部左转/右转30度，刘海与侧发不穿透脸颊。
- 头部左倾/右倾15度，颈部与卫衣领口无缝隙。
- 双眼完全闭合、半闭眼与单眼眨眼。
- 视线移动到左上/右下，虹膜不越出眼白。
- 张嘴与微笑切换，口腔内部不破损。
- 身体左右倾斜10度并呼吸，卫衣下摆与短裤不露底。
- 手臂前后轻微摆动，袖口与手腕连接连续。
- 头发、帽绳在头部/身体运动下产生滞后摆动。
- 腿部和脚部小幅度移动，长袜与鞋无接缝。

## 风险
- 面部被刘海与侧发大面积遮挡，补画范围不足会导致头部转动时额头/脸颊断层。
- 眼睛高光若与虹膜同层会随视线移动，建议拆为独立高光层。
- 袖口与手部粘连，手腕补画不足会导致摆臂漏底。
- 卫衣下摆遮挡短裤上缘，需补画短裤和躯干内侧。
- 过膝袜与短裤之间裸露皮肤窄，皮肤层与袜层需精确遮罩。
- 鞋与袜重叠，脚踝补画不足会导致脚部移动穿帮。
- 正面图缺少侧面和背面信息，后发、帽后和腋下需合理推断。
- 腿部为并拢站立，左右腿层需避免互相穿插。
