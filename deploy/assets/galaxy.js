// ===== 星河引擎（原创实现，无第三方依赖） =====
// 固定种子星种：hero 处聚成圆形螺旋星系，三成星种常驻全屏铺底填补四角，
// 滚动散作整页星野，同一组星种每次都落回原处；
// 辉光精灵 + 加色混合绘制；流星随机掠过；标签页隐藏暂停；
// 首屏静置后星种打散汇聚成字形轮播（点点 → 点击涟漪），到点或滚动时再打散出场；
// 展示期快速滑动可犁散字形并自动复原，出场时整字先 3D 倾转再炸散；
// 指针交互：悬浮斥力、按住引力井、近距星点点亮、拖尾与高速粒子彗尾；
// prefers-reduced-motion 时只画一帧成形星系
(function () {
  var canvas = document.getElementById('galaxy-canvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  // 极老浏览器或文本模式下拿不到 2d 上下文：静默退化为纯静态页面
  if (!ctx) return;

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // dpr 上限 1.5：星点皆为柔光贴图，无需像素级精度，却省下约四成填充开销
  var dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  var isSmall = false;

  var width = 0;
  var height = 0;
  var particles = [];
  var sprites = {};
  var meteors = [];
  var trail = [];      // 光标快速移动时沉淀的拖尾采样点
  var running = false;
  var morph = 0;           // 0 = 星系成形，1 = 散作星野
  var par = { x: 0, y: 0 };    // 视差平滑值（归一化 -0.5 ~ 0.5）
  var nextMeteorAt = 0;

  // 首屏聚形轮播：静置后打散汇聚成字形，到点或滚动时打散出场
  var shapes = [];             // 每项为 { pts: 点云, cx/cy: 字形中心 }
  var shapeState = -1;         // -1 = 星系态，>=0 为 shapes 下标
  var shapePhase = 'none';     // none = 星系/停留，enter = 汇聚中，exit = 打散出场中
  var phaseAt = 0;             // 当前相位的起点时刻
  var phaseDur = 1;            // 当前相位的时长
  var shapeUntil = 0;          // 当前字形的展示截止时刻
  var showFade = 0;            // 展示期淡入淡出系数（只用于核心辉光等背景元素）
  var SHAPE_HOLD = 8000;       // 字形展示时长
  var GALAXY_HOLD = 8500;      // 两轮字形之间回到星系的时长
  var IDLE_NEED = 3500;        // 判定悬停静置所需的无指针移动时长
  var SHAPE_EXTEND = 1500;     // 指针未静置时展示的顺延步长
  var ENTER_DUR = 2600;        // 打散汇聚入场时长
  var EXIT_DUR = 2100;         // 打散出场时长
  var nextShapeAt = GALAXY_HOLD;
  var lastPointerMoveAt = 0;
  var showCX = 0;              // 当前字形的中心（出场 3D 倾转的旋转轴心）
  var showCY = 0;
  var tiltOn = false;          // 出场前半程开启整字 3D 倾转
  var tiltCos = 1;
  var tiltSin = 0;
  var tiltSquash = 1;
  var tiltGain = 1;            // 倾转强度：入场被打断时按已完成度缩放，避免首帧突跳

  var SEED = 20260923;

  // 星系与聚形字形的中心：桌面端固定落在 hero 右侧展示区（约 56%~95% 宽、19%~83% 高），
  // 小屏没有左右分栏，居中即可
  function galaxyCenter() {
    return isSmall
        ? { x: width * 0.5, y: height * 0.46 }
        : { x: width * 0.75, y: height * 0.5 };
  }

  // 指针状态：tx/ty 为目标像素坐标，x/y 缓动跟随；down 表示按住形成引力井
  var pointer = { x: -9999, y: -9999, tx: -9999, ty: -9999, inside: false, down: false, touch: false };
  var forceBoost = 1;          // 展示期指针力场放大系数（随滑动速度增大，滑过字形能犁出缺口）

  // 交互半径按屏宽分档：小屏力场收窄，避免盖住正文与触控目标
  var REPEL_R = 130;
  var HI_R = 160;
  // 辉光贴图随视口宽度等比缩放的系数（1920 宽为基准 1.0），由 tuneByWidth 按当前视口计算
  var sizeScale = 1;

  function tuneByWidth() {
    isSmall = width < 736;
    REPEL_R = isSmall ? 90 : 130;
    HI_R = isSmall ? 110 : 160;
    sizeScale = Math.max(0.55, Math.min(1.6, width / 1920));
  }

  // 可复现的伪随机（mulberry32）：同一组星种，每次都落回原处
  function mulberry32(seed) {
    return function () {
      seed |= 0;
      seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  // 高斯散布（Box-Muller）
  function gauss(rand) {
    var u = Math.max(rand(), 1e-9);
    var v = rand();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  // 星点配色：白 / 品牌青绿 / 天蓝 / 紫 / 琥珀（暗面上多色星芒）
  var PALETTE = [
    { color: '255, 255, 255', weight: 0.46 },
    { color: '45, 212, 191', weight: 0.18 },
    { color: '125, 211, 252', weight: 0.14 },
    { color: '167, 139, 250', weight: 0.13 },
    { color: '251, 191, 36', weight: 0.09 }
  ];

  function pickColor(rand) {
    var r = rand(), acc = 0;
    for (var i = 0; i < PALETTE.length; i++) {
      acc += PALETTE[i].weight;
      if (r <= acc) return PALETTE[i].color;
    }
    return PALETTE[0].color;
  }

  // 辉光精灵：径向渐变预渲染，逐帧只做贴图
  function makeSprite(color) {
    var size = 64;
    var sprite = document.createElement('canvas');
    sprite.width = size;
    sprite.height = size;
    var sctx = sprite.getContext('2d');
    var g = sctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, 'rgba(' + color + ', 1)');
    g.addColorStop(0.25, 'rgba(' + color + ', 0.55)');
    g.addColorStop(0.6, 'rgba(' + color + ', 0.12)');
    g.addColorStop(1, 'rgba(' + color + ', 0)');
    sctx.fillStyle = g;
    sctx.fillRect(0, 0, size, size);
    return sprite;
  }

  function buildParticles() {
    particles = [];
    var rand = mulberry32(SEED);
    // 小屏按比例缩减粒子数，保证中低端手机帧率
    var count = isSmall ? 2400 : 6400;
    var center = galaxyCenter();
    var cx = center.x;
    var cy = center.y;
    // 桌面端半径按右侧展示区约束（区宽约 0.48 倍视口、区高约 0.94 倍视口高），
    // 旋臂主体落在展示区内、稀疏臂梢允许轻微越出；小屏退回中心到四边的距离约束
    var maxR = isSmall
        ? Math.max(120, Math.min(cx, width - cx, cy, height - cy) - 30)
        : Math.min(width * 0.24, height * 0.47);
    var arms = 2;
    var twist = 2.4 * Math.PI;

    for (var i = 0; i < count; i++) {
      // 三成粒子作环境星常驻铺满全屏，填补圆形星系与字形四周的空白
      var ambient = rand() < 0.3;
      var gx, gy, r, alpha;

      if (ambient) {
        gx = rand() * width;
        gy = rand() * height;
        r = 0.4 + rand() * 1.0;
        alpha = 0.25 + rand() * 0.45;
      } else {
        var isCore = rand() < 0.14;
        var armPos = rand();
        // 对数感螺旋：粒子沿旋臂分布，基础角由臂位决定
        var radius = isCore ? Math.pow(rand(), 1.6) * maxR * 0.18 : (0.1 + 0.9 * Math.pow(armPos, 0.65)) * maxR;
        // 六成粒子近乎零散布压在臂心线上勾勒锐利轨线，其余作弥散晕填充臂面
        var onLine = rand() < 0.6;
        var arm = i % arms;
        var armSpread;
        var spread;
        if (isCore) {
          armSpread = 0.5;
          spread = 8;
        } else if (onLine) {
          armSpread = 0.02 + (1 - radius / maxR) * 0.04;
          spread = 2;
        } else {
          armSpread = 0.16 + (1 - radius / maxR) * 0.22;
          spread = 6 + (radius / maxR) * 18;
        }
        var theta = (arm / arms) * Math.PI * 2 + (radius / maxR) * twist + gauss(rand) * armSpread;

        gx = cx + Math.cos(theta) * radius + gauss(rand) * spread;
        gy = cy + Math.sin(theta) * radius + gauss(rand) * spread;
        // 星系粒子纤细，细星点叠加出丝绢质感（环境星粗细见 frame 里的贴图分档）
        r = isCore ? 0.8 + rand() * 1.2 : 0.35 + Math.pow(rand(), 2) * 1.7;
        alpha = 0.35 + rand() * 0.65;
      }

      var color = pickColor(rand);

      particles.push({
        gx: gx,
        gy: gy,
        fx: rand() * width,
        fy: rand() * height * 1.15,
        r: r,
        color: color,
        // 逐粒子缓存辉光精灵，逐帧免去做哈希查找
        sprite: sprites[color],
        alpha: alpha,
        phase: rand() * Math.PI * 2,
        speed: 0.6 + rand() * 1.8,
        parallax: 0.4 + rand() * 0.6,
        ambient: ambient,
        // 力场偏移（弹簧回位）与上一帧位置（彗尾起点）
        ox: 0,
        oy: 0,
        ovx: 0,
        ovy: 0,
        px: null,
        py: null,
        // 聚形目标点：默认落在星系中心，防止过渡期出现 NaN 或飞向角落
        sx: cx,
        sy: cy,
        ax: cx,
        ay: cy,
        // 聚形时间线：外抛控制点与错峰起步的随机种子
        wx: cx,
        wy: cy,
        delay: rand(),
        flare: rand()
      });
    }
  }

  function buildSprites() {
    sprites = {};
    PALETTE.forEach(function (p) {
      sprites[p.color] = makeSprite(p.color);
    });
    sprites.core = makeSprite('224, 255, 250');
    // 品牌青绿 #2dd4bf：悬浮点亮、拖尾与流星头部共用
    sprites.hi = makeSprite('45, 212, 191');
  }

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    // dpr 在此处读取而非事件回调里：保证重建时用的永远是当前值
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    tuneByWidth();
    buildParticles();
    // 减动效没有聚形轮播，不必付出离屏采样成本
    if (!reduceMotion) buildShapes();

    // 尺寸变了：字形点位全部失效，正在展示的内容直接散回星系态
    if (shapeState >= 0 || shapePhase !== 'none') {
      shapeState = -1;
      shapePhase = 'none';
      nextShapeAt = performance.now() + GALAXY_HOLD;
    }
  }

  function easeInOut(t) {
    return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  }

  // 字形点云：把文字/图标画在离屏画布上按步长采样，得到聚形目标点。
  // 桌面端字形缩小到展示区内后，用更密采样与更高抽稀上限保证笔画连绵不断档
  function samplePoints(draw) {
    var off = document.createElement('canvas');
    off.width = Math.max(1, width);
    off.height = Math.max(1, height);
    var octx = off.getContext('2d');
    draw(octx);

    var pts = [];
    var data = octx.getImageData(0, 0, off.width, off.height).data;
    var stride = Math.max(3, Math.round(Math.min(width, height) / (isSmall ? 220 : 320)));
    for (var y = 0; y < off.height; y += stride) {
      for (var x = 0; x < off.width; x += stride) {
        if (data[(y * off.width + x) * 4 + 3] <= 128) continue;

        pts.push({ x: x, y: y });
      }
    }

    // 点数封顶：超出时按间隔抽稀，保证字形整体都被星点覆盖
    // （不抽稀时只会用掉按行扫描的前一段点，字形底部会空缺）；上限不超过可用粒子数
    var cap = isSmall ? 900 : 4300;
    if (pts.length > cap) {
      var thinned = [];
      var stepPt = Math.ceil(pts.length / cap);
      for (var j = 0; j < pts.length; j += stepPt) {
        thinned.push(pts[j]);
      }
      pts = thinned;
    }
    return pts;
  }

  // 点击涟漪标记：中心点 + 两圈涟漪 + 左下两颗小点，呼应 logo 的"点点"意象
  function drawClickMark(octx, cx, cy, s) {
    octx.fillStyle = '#ffffff';
    octx.strokeStyle = '#ffffff';
    octx.lineCap = 'round';
    octx.lineWidth = Math.max(2, s * 0.045);

    function dot(x, y, r) {
      octx.beginPath();
      octx.arc(x, y, r, 0, Math.PI * 2);
      octx.fill();
    }

    function ring(x, y, r) {
      octx.beginPath();
      octx.arc(x, y, r, 0, Math.PI * 2);
      octx.stroke();
    }

    dot(cx, cy, s * 0.09);
    ring(cx, cy, s * 0.2);
    ring(cx, cy, s * 0.32);
    dot(cx - s * 0.38, cy + s * 0.34, s * 0.06);
    dot(cx - s * 0.28, cy + s * 0.44, s * 0.045);
  }

  function buildShapes() {
    shapes = [];
    // 与星系中心保持一致，聚形字形同样落在展示区内
    var center = galaxyCenter();
    var cx = center.x;
    var cy = center.y;
    var unit = Math.min(width, height);

    var textPts = samplePoints(function (octx) {
      octx.fillStyle = '#ffffff';
      // 中文两字宽约 2 倍字号；桌面端字形需完整落在展示区内，额外受 0.45 倍视口高约束
      var fontSize = isSmall
          ? Math.min(unit * 0.52, width * 0.42)
          : Math.min(unit * 0.52, width * 0.42, height * 0.45);
      octx.font = '800 ' + Math.round(fontSize)
          + 'px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif';
      octx.textAlign = 'center';
      octx.textBaseline = 'middle';
      octx.fillText('点点', cx, cy);
    });
    if (textPts.length > 0) shapes.push({ pts: textPts, cx: cx, cy: cy });

    var markSize = isSmall
        ? Math.min(unit * 0.46, width * 0.55)
        : Math.min(unit * 0.46, width * 0.55, height * 0.45);
    var markPts = samplePoints(function (octx) {
      drawClickMark(octx, cx, cy, markSize);
    });
    if (markPts.length > 0) shapes.push({ pts: markPts, cx: cx, cy: cy });
  }

  // 逐粒子错峰进度：delay 决定起步时间，全员在同一时刻就位
  function staggerK(raw, delay) {
    var k = (raw - delay * 0.45) / 0.55;
    if (k <= 0) return 0;
    if (k >= 1) return 1;

    return k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
  }

  // 入场：从粒子当前位置出发，经外抛控制点打散后汇聚成字形
  function enterShape(k, now) {
    var shape = shapes[k];
    var pts = shape.pts;
    shapeState = k;
    shapePhase = 'enter';
    phaseAt = now;
    phaseDur = ENTER_DUR;
    shapeUntil = now + ENTER_DUR + SHAPE_HOLD;
    showCX = shape.cx;
    showCY = shape.cy;

    // 环境星不参与聚形；剩余粒子按比例均摊到全部字形点上，首尾都覆盖不缺笔画
    var total = 0;
    for (var i = 0; i < particles.length; i++) {
      if (!particles[i].ambient) total++;
    }

    var mapped = 0;
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      if (p.ambient) continue;   // 环境星不参与聚形，保持全屏铺底

      var pt = pts[Math.min(pts.length - 1, Math.floor(mapped * pts.length / total))];
      mapped++;
      // 起点用上一帧绘制位置：滚动打断入场时也能就地接续散场
      p.ax = p.px !== null ? p.px : pt.x;
      p.ay = p.py !== null ? p.py : pt.y;
      p.sx = pt.x;
      p.sy = pt.y;
      // 控制点沿字形中心向外抛、带切向抖动，路径呈"打散再汇聚"的弧线；
      // 外抛距离按视口缩放，窄屏下不把粒子甩出屏幕
      var dx = pt.x - shape.cx;
      var dy = pt.y - shape.cy;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var push = (70 + p.flare * 240) * Math.min(1, Math.min(width, height) / 900);
      p.wx = pt.x + (dx / dist) * push + (-dy / dist) * (p.flare - 0.5) * 140;
      p.wy = pt.y + (dy / dist) * push + (dx / dist) * (p.flare - 0.5) * 140;
    }
  }

  // 出场：从当前位置经外抛弧线打散，飞回星系/星野的实时位置
  function scatterOut(now) {
    // 入场中途被打断时，倾转按已完成度缩放，避免星野首帧被满幅扭曲
    tiltGain = shapePhase === 'enter'
        ? Math.min(1, Math.max(0, (now - phaseAt) / ENTER_DUR))
        : 1;
    shapePhase = 'exit';
    phaseAt = now;
    phaseDur = EXIT_DUR;
    shapeState = -1;
    nextShapeAt = now + EXIT_DUR + GALAXY_HOLD;

    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      if (p.ambient) continue;

      p.ax = p.px !== null ? p.px : p.sx;
      p.ay = p.py !== null ? p.py : p.sy;
    }
  }

  // 展示到期：切下一个字形；一轮播完则打散散场
  function advanceOrScatter(now) {
    shapeState++;
    if (shapeState < shapes.length) {
      enterShape(shapeState, now);
      return;
    }

    scatterOut(now);
  }

  // 首屏聚形轮播状态机
  function updateShapeCarousel(now, e) {
    // 入场中滚动：就地打散出场，星点从半路炸开飞回
    if (shapePhase === 'enter') {
      if (e >= 0.12) scatterOut(now);
      return;
    }
    if (shapePhase === 'exit') return;

    if (reduceMotion || shapes.length === 0) {
      shapeState = -1;
      return;
    }

    if (shapeState >= 0) {
      if (e >= 0.12) {
        // 展示期滚动：立即打散出场
        scatterOut(now);
      } else if (now > shapeUntil) {
        advanceOrScatter(now);
      } else if (now - lastPointerMoveAt <= IDLE_NEED) {
        // 指针仍在活动：顺延展示，等静置再切换
        shapeUntil = Math.max(shapeUntil, now + SHAPE_EXTEND);
      }
      return;
    }

    // 星系态：滚离首屏不聚形（否则星野会在正文后空转聚散）；静置且到点才入场
    if (e < 0.12 && now > nextShapeAt && now - lastPointerMoveAt > IDLE_NEED) {
      enterShape(0, now);
    }
  }

  // 指针力场：悬浮为斥力，按住径向反向成引力井；只作用于弹簧偏移，不破坏形体。
  // 展示期 forceBoost 放大且去掉切向分量：快速滑过能犁出缺口但不让字形打转
  function applyPointerForce(p, x, y) {
    var reach = forceBoost > 1 ? REPEL_R * 1.5 : REPEL_R;
    var fex = x + p.ox - pointer.x;
    var fey = y + p.oy - pointer.y;
    var fd2 = fex * fex + fey * fey;
    if (fd2 >= reach * reach || fd2 <= 0.01) return;

    var fd = Math.sqrt(fd2);
    var force = (1 - fd / reach) * forceBoost;
    var nx = fex / fd;
    var ny = fey / fd;
    var radial = pointer.down ? -1.5 : 1.1;
    var swirl = 0.5;
    if (pointer.down) {
      swirl = 1.15;
    } else if (forceBoost > 1) {
      // 展示期去掉切向分量：快速滑过能犁出缺口但不让字形打转
      swirl = 0;
    }
    p.ovx += (nx * radial - ny * swirl) * force;
    p.ovy += (ny * radial + nx * swirl) * force;
  }

  // 悬浮高亮：光标附近的星点被点亮，越近越亮
  function applyPointerGlow(p, x, y, alpha, size) {
    var hdx = x - pointer.x;
    var hdy = y - pointer.y;
    var hd2 = hdx * hdx + hdy * hdy;
    if (hd2 >= HI_R * HI_R) return;

    var brighten = 1 - Math.sqrt(hd2) / HI_R;
    if (brighten > 0.15) {
      ctx.globalAlpha = Math.min(1, alpha * brighten * 2.2);
      var hs = size * (1 + brighten * 0.6);
      ctx.drawImage(sprites.hi, x - hs / 2, y - hs / 2, hs, hs);
    }
  }

  // 彗尾：粒子帧间位移超过阈值时描一段拖痕；返回是否消耗了一次预算
  function drawStreak(p, x, y, alpha) {
    var sdx = x - p.px;
    var sdy = y - p.py;
    if (sdx * sdx + sdy * sdy <= 9) return false;

    ctx.strokeStyle = 'rgba(' + p.color + ', ' + (alpha * 0.55).toFixed(3) + ')';
    ctx.lineWidth = Math.max(0.5, p.r * 0.9 * sizeScale);
    ctx.beginPath();
    ctx.moveTo(p.px, p.py);
    ctx.lineTo(x, y);
    ctx.stroke();
    return true;
  }

  var poseX = 0;
  var poseY = 0;

  // 聚形时间线：按错峰进度计算粒子在贝塞尔路径上的位置，写入 poseX/poseY。
  // 入场/停留的终点是字形点，出场的终点是当帧基准位置
  function applyShapePose(p, raw, bx, by) {
    var prog = staggerK(raw, p.delay);
    if (prog >= 1) {
      poseX = shapePhase === 'exit' ? bx : p.sx;
      poseY = shapePhase === 'exit' ? by : p.sy;
      return;
    }

    // 出场前半程：整字绕中心 3D 倾转（旋转 + 纵向透视压扁），让笔画先"立起来"再炸散
    var stX = p.ax;
    var stY = p.ay;
    if (tiltOn) {
      var rx = p.ax - showCX;
      var ry = (p.ay - showCY) * tiltSquash;
      stX = showCX + rx * tiltCos - ry * tiltSin;
      stY = showCY + rx * tiltSin + ry * tiltCos;
    }

    var inv = 1 - prog;
    var endX = shapePhase === 'exit' ? bx : p.sx;
    var endY = shapePhase === 'exit' ? by : p.sy;
    poseX = inv * inv * stX + 2 * inv * prog * p.wx + prog * prog * endX;
    poseY = inv * inv * stY + 2 * inv * prog * p.wy + prog * prog * endY;
  }

  var startT = 0;
  // 彗尾描边有逐帧预算上限，滚动形变瞬间几千颗粒子同动时成本可控
  var STREAK_BUDGET = 700;
  var STREAK_BUDGET_SMALL = 350;

  // 深空底色 + 三团星云整层缓存为离屏画布：全屏渐变填充是每帧最大的固定开销，
  // 仅当形变进度/视差/尺寸跨过量化阈值时才重绘，静止时一帧只做一次贴图
  var bgCache = null;
  var bgKey = { e: -1, x: -1, y: -1 };

  function drawBackground(e, cx, cy) {
    var keyE = Math.round(e * 40);
    var keyX = Math.round(cx / 8);
    var keyY = Math.round(cy / 8);
    var cw = Math.round(width * dpr);
    var ch = Math.round(height * dpr);

    if (bgCache && bgKey.e === keyE && bgKey.x === keyX && bgKey.y === keyY
        && bgCache.width === cw && bgCache.height === ch) {
      ctx.drawImage(bgCache, 0, 0, width, height);
      return;
    }
    bgKey.e = keyE;
    bgKey.x = keyX;
    bgKey.y = keyY;

    if (!bgCache) bgCache = document.createElement('canvas');
    if (bgCache.width !== cw || bgCache.height !== ch) {
      bgCache.width = cw;
      bgCache.height = ch;
    }
    var bctx = bgCache.getContext('2d');
    bctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    bctx.globalCompositeOperation = 'source-over';
    bctx.fillStyle = '#060a14';
    bctx.fillRect(0, 0, width, height);

    // 两团星云共用同一作用半径（第三团单独 0.7），先算好避免超长行
    var nebulaRange = Math.max(width, height) * 0.62;
    var nebula1 = bctx.createRadialGradient(cx, cy, 0, cx, cy, nebulaRange);
    nebula1.addColorStop(0, 'rgba(12, 58, 62, ' + (0.5 * (1 - e * 0.55)).toFixed(3) + ')');
    nebula1.addColorStop(1, 'rgba(12, 58, 62, 0)');
    bctx.fillStyle = nebula1;
    bctx.fillRect(0, 0, width, height);

    var nebula2 = bctx.createRadialGradient(0, height, 0, 0, height, Math.max(width, height) * 0.7);
    nebula2.addColorStop(0, 'rgba(46, 16, 58, ' + (0.4 * (1 - e * 0.5)).toFixed(3) + ')');
    nebula2.addColorStop(1, 'rgba(46, 16, 58, 0)');
    bctx.fillStyle = nebula2;
    bctx.fillRect(0, 0, width, height);

    // 右上角第三团深青星云，平衡左下紫团，滚动散场后随之减淡
    var nebula3 = bctx.createRadialGradient(width, 0, 0, width, 0, nebulaRange);
    nebula3.addColorStop(0, 'rgba(10, 44, 58, ' + (0.32 * (1 - e * 0.4)).toFixed(3) + ')');
    nebula3.addColorStop(1, 'rgba(10, 44, 58, 0)');
    bctx.fillStyle = nebula3;
    bctx.fillRect(0, 0, width, height);

    ctx.drawImage(bgCache, 0, 0, width, height);
  }

  function frame(now) {
    if (!startT) startT = now;
    // 自愈：错过 resize 事件（缩放往返、宿主窗格调整）导致视口尺寸失配时立即重建，
    // 否则画布坐标系与光标坐标系脱节，交互效果会出现位置偏移
    if (width !== window.innerWidth || height !== window.innerHeight) {
      resize();
    }
    var t = (now - startT) / 1000;

    // 形变进度：滚动过 hero 的过程对应 0 → 1
    var target = Math.min(1, Math.max(0, window.scrollY / (window.innerHeight * 1.15)));
    morph += (target - morph) * 0.08;
    var e = easeInOut(morph);

    // 指针位置缓动跟随；快速移动时沉淀拖尾采样点。
    // 残余距离不足 1px 时直接贴合，保证静止时力场/高亮与光标严格对位
    var pdx = pointer.tx - pointer.x;
    var pdy = pointer.ty - pointer.y;
    if (pdx * pdx + pdy * pdy < 1) {
      pointer.x = pointer.tx;
      pointer.y = pointer.ty;
    } else {
      pointer.x += pdx * 0.3;
      pointer.y += pdy * 0.3;
    }
    if (pointer.inside && Math.sqrt(pdx * pdx + pdy * pdy) > 5) {
      trail.push({ x: pointer.x, y: pointer.y, life: 1 });
      if (trail.length > 16) trail.shift();
    }

    // 视差：指针离开视口后缓慢归位
    var ptx = pointer.inside ? pointer.x / width - 0.5 : 0;
    var pty = pointer.inside ? pointer.y / height - 0.5 : 0;
    par.x += (ptx - par.x) * 0.05;
    par.y += (pty - par.y) * 0.05;

    var center = galaxyCenter();
    var galaxyCx = center.x + par.x * 18;
    var galaxyCy = center.y + par.y * 14;
    var spin = t * 0.03 * (1 - e);
    // 旋转角整帧恒定，三角函数提到粒子循环外
    var spinCos = Math.cos(spin);
    var spinSin = Math.sin(spin);

    // 首屏聚形轮播：静置后打散汇聚成字形，到点或滚动时打散出场
    updateShapeCarousel(now, e);
    var shapeRaw = shapePhase === 'none' ? 1 : (now - phaseAt) / phaseDur;
    if (shapePhase !== 'none' && shapeRaw >= 1) {
      // 相位播完：入场转停留，出场回归星系态
      shapePhase = 'none';
    }
    var showTarget = shapeState >= 0 || shapePhase !== 'none' ? 1 : 0;
    showFade += (showTarget - showFade) * 0.04;

    // 展示期把指针力场放大：快速滑过字形能犁出缺口，随后靠弹簧自动复原
    var pointerSpeed = Math.sqrt(pdx * pdx + pdy * pdy);
    forceBoost = shapeState >= 0 && shapePhase === 'none'
        ? 1 + Math.min(1.6, pointerSpeed * 0.05)
        : 1;

    // 出场的整字 3D 倾转随散场进度渐入：从正立平滑立起再炸散，不瞬间摆斜
    tiltOn = shapePhase === 'exit';
    if (tiltOn) {
      var tilt = Math.min(1, shapeRaw / 0.4) * tiltGain;
      tiltCos = Math.cos(0.5 * tilt);
      tiltSin = Math.sin(0.5 * tilt);
      tiltSquash = 1 - 0.3 * tilt;
    }

    // 深空底色 + 星云辉光：整层离屏缓存（见 drawBackground），一帧一次贴图
    ctx.globalCompositeOperation = 'source-over';
    drawBackground(e, galaxyCx, galaxyCy);

    // 粒子主体：加色混合发光
    ctx.globalCompositeOperation = 'lighter';

    var streakBudget = isSmall ? STREAK_BUDGET_SMALL : STREAK_BUDGET;
    // 展示期弹簧更硬：犁散后干净复原不回弹
    var springStiff = shapeState >= 0 ? 0.04 : 0.028;
    var springDamp = shapeState >= 0 ? 0.85 : 0.9;
    var i, p;

    for (i = 0; i < particles.length; i++) {
      p = particles[i];

      // 星系态：绕核心缓旋；星野态：各自微漂
      // 环境星常驻铺底：不随核心旋转（全屏矩形均匀场一旦刚体旋转会转出窗口）
      var rx, ry;
      if (p.ambient) {
        rx = p.gx;
        ry = p.gy;
      } else {
        var ogx = p.gx - galaxyCx;
        var ogy = p.gy - galaxyCy;
        rx = galaxyCx + ogx * spinCos - ogy * spinSin;
        ry = galaxyCy + ogx * spinSin + ogy * spinCos;
      }

      var fx = p.fx + Math.sin(t * 0.12 + p.phase) * 6;
      var fy = p.fy + Math.cos(t * 0.1 + p.phase) * 6;

      var x = rx + (fx - rx) * e - par.x * 16 * p.parallax;
      var y = ry + (fy - ry) * e - par.y * 10 * p.parallax;

      var bx = x;
      var by = y;

      // 聚形时间线：入场汇聚 / 停留 / 打散出场共用逐粒子错峰贝塞尔路径
      // 环境星不参与，始终铺满全屏
      if (!p.ambient && (shapeState >= 0 || shapePhase !== 'none')) {
        applyShapePose(p, shapeRaw, bx, by);
        x = poseX;
        y = poseY;
      }

      if (pointer.inside) applyPointerForce(p, x, y);

      // 弹簧回位：低刚度轻阻尼，衰减慢所以带弹性余韵，展示期用更硬参数干净复原；限幅防过冲
      p.ovx = (p.ovx - p.ox * springStiff) * springDamp;
      p.ovy = (p.ovy - p.oy * springStiff) * springDamp;
      if (p.ox > 260 || p.ox < -260) {
        p.ox = Math.max(-260, Math.min(260, p.ox));
        p.ovx *= 0.5;
      }
      if (p.oy > 260 || p.oy < -260) {
        p.oy = Math.max(-260, Math.min(260, p.oy));
        p.ovy *= 0.5;
      }
      p.ox += p.ovx;
      p.oy += p.ovy;

      x += p.ox;
      y += p.oy;

      var twinkle = 0.55 + 0.45 * Math.sin(t * p.speed + p.phase);
      var alpha = p.alpha * twinkle * (1 - e * 0.35);
      // 贴图尺寸 = 粒子半径 × 视口自适应缩放；环境星与星系粒子分两档粗细。
      // 聚形展示期字形点密度低于星系，非环境星贴图放大 1.35 倍补足单点亮度
      var glyphBoost = !p.ambient && (shapeState >= 0 || shapePhase !== 'none') ? 1.35 : 1;
      var size = p.r * sizeScale * (p.ambient ? 5.6 : 4.3) * glyphBoost;

      // 视口外的粒子只更新上一帧位置，跳过绘制与高亮，省掉出界贴图开销
      var half = size * 0.5;
      if (x < -half || x > width + half || y < -half || y > height + half) {
        p.px = x;
        p.py = y;
        continue;
      }

      // 高速运动的粒子拖出彗尾（滚动形变、力场掠过时最明显）
      if (p.px !== null && streakBudget > 0 && drawStreak(p, x, y, alpha)) {
        streakBudget--;
      }
      p.px = x;
      p.py = y;

      ctx.globalAlpha = alpha;
      ctx.drawImage(p.sprite, x - half, y - half, size, size);

      if (pointer.inside) applyPointerGlow(p, x, y, alpha, size);
    }

    // 星系核心辉光（成形时；带缓慢脉动，展示期淡出让位给字形）
    if (e < 0.95) {
      var pulse = 1 + 0.06 * Math.sin(t * 1.5);
      var coreSize = Math.min(width, height) * (0.32 - e * 0.18) * pulse;
      ctx.globalAlpha = (1 - e) * (1 - showFade);
      ctx.drawImage(sprites.core, galaxyCx - coreSize / 2, galaxyCy - coreSize / 2, coreSize, coreSize);
      ctx.globalAlpha = (1 - e) * 0.9 * (1 - showFade);
      var coreSize2 = Math.min(width, height) * 0.07 * pulse;
      ctx.drawImage(sprites.core, galaxyCx - coreSize2 / 2, galaxyCy - coreSize2 / 2, coreSize2, coreSize2);
    }

    // 光标拖尾：采样点渐次熄灭
    for (i = trail.length - 1; i >= 0; i--) {
      var drop = trail[i];
      drop.life -= 0.06;
      if (drop.life <= 0) {
        trail.splice(i, 1);
        continue;
      }
      var dropSize = 34 * sizeScale * drop.life;
      ctx.globalAlpha = drop.life * 0.28;
      ctx.drawImage(sprites.hi, drop.x - dropSize / 2, drop.y - dropSize / 2, dropSize, dropSize);
    }

    // 流星
    if (now > nextMeteorAt && !reduceMotion) {
      meteors.push({
        x: width * (0.3 + Math.random() * 0.6),
        y: -40,
        vx: -(2.2 + Math.random() * 2.4),
        vy: 3.4 + Math.random() * 2.2,
        life: 1
      });
      nextMeteorAt = now + 1800 + Math.random() * 2400;
    }

    for (i = meteors.length - 1; i >= 0; i--) {
      var m = meteors[i];
      m.x += m.vx;
      m.y += m.vy;
      m.life -= 0.012;
      if (m.life <= 0 || m.y > height + 60) {
        meteors.splice(i, 1);
        continue;
      }
      var tail = 14;
      var grad = ctx.createLinearGradient(m.x, m.y, m.x - m.vx * tail, m.y - m.vy * tail);
      grad.addColorStop(0, 'rgba(200, 235, 255, ' + (0.85 * m.life).toFixed(3) + ')');
      grad.addColorStop(1, 'rgba(200, 235, 255, 0)');
      ctx.globalAlpha = 1;
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(m.x, m.y);
      ctx.lineTo(m.x - m.vx * tail, m.y - m.vy * tail);
      ctx.stroke();
      ctx.globalAlpha = m.life * 0.9;
      var mSize = 22 * sizeScale;
      ctx.drawImage(sprites.hi, m.x - mSize / 2, m.y - mSize / 2, mSize, mSize);
    }

    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }

  function loop(now) {
    if (!running) return;
    frame(now);
    requestAnimationFrame(loop);
  }

  function start() {
    if (running) return;
    running = true;
    // 不重置 startT：切回标签页时 t 保持连续，星系旋转角不回卷
    requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
  }

  // resize 防抖：桌面拖拽与移动端地址栏收展会连发，重建上千颗粒子不宜逐帧做
  var resizeTimer = 0;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      resize();
      if (reduceMotion) {
        // 减动效模式：resize 后重画成形帧
        morph = 0;
        frame(performance.now());
      }
    }, 180);
  }, { passive: true });

  // 记录指针目标位置；从视口外重新进入或触屏首按直接落位，不允许高亮从旧位置飞入
  function trackPointer(e) {
    lastPointerMoveAt = performance.now();
    if (!pointer.inside) {
      pointer.x = e.clientX;
      pointer.y = e.clientY;
      trail.length = 0;
    }
    pointer.tx = e.clientX;
    pointer.ty = e.clientY;
    pointer.inside = true;
  }

  // 收起指针存在感：高亮随之停止，力场偏移交给弹簧自行回位
  function releasePointer() {
    pointer.inside = false;
    pointer.down = false;
  }

  if (window.PointerEvent) {
    window.addEventListener('pointermove', function (e) {
      trackPointer(e);
    }, { passive: true });

    window.addEventListener('pointerdown', function (e) {
      pointer.touch = e.pointerType === 'touch';
      trackPointer(e);
      pointer.down = true;
    }, { passive: true });

    window.addEventListener('pointerup', function () {
      pointer.down = false;
      // 触屏没有悬浮态，抬手即收场
      if (pointer.touch) releasePointer();
    }, { passive: true });

    window.addEventListener('pointercancel', releasePointer, { passive: true });
  } else {
    // 无 Pointer Events 的老浏览器退化到鼠标事件；触屏仅失去力场交互，不影响渲染
    document.addEventListener('mousemove', trackPointer, { passive: true });

    document.addEventListener('mousedown', function (e) {
      trackPointer(e);
      pointer.down = true;
    });

    document.addEventListener('mouseup', function () {
      pointer.down = false;
    });
  }

  document.documentElement.addEventListener('mouseleave', releasePointer);

  window.addEventListener('blur', releasePointer);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      stop();
    } else if (!reduceMotion) {
      // 减动效模式没有动画循环，切回标签页不得将其意外启动
      start();
    }
  });

  buildSprites();
  resize();

  if (reduceMotion) {
    morph = 0;
    frame(performance.now());
  } else {
    start();
  }
})();
