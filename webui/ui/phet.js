/*
 * PhET 互动仿真（M8）：把随包内置的 3 个模拟放进一个对话框里用 iframe 打开。
 *
 * 为什么要 iframe：PhET 的模拟是**自包含的单文件 HTML**（约 2.5~3.3 MB，自带 JS 和素材），
 * 不是模块、也没法拆开重写；iframe 是最省事也最忠于原样的做法（不改它的代码）。
 *
 * 三个模拟都是 CC BY-NC 4.0（署名 - 非商业），只能课堂教学用；
 * 界面上给一行署名，详细说明在 vendor/phet/README.txt。
 *
 * 懒加载：只在第一次点开某个模拟时才给 iframe 设 src（不然一进页面就吞 8 MB）。
 */
(function () {
  'use strict';

  var SIMS = [
    { key: 'circuit-construction-kit-dc', name: '电路组建实验:直流', subject: '物理',
      note: '拖电池、导线、灯泡搭电路，实时看电流和电压（和本程序的电路画板可以对照着用）' },
    { key: 'neuron', name: '神经元（静息/动作电位）', subject: '生物',
      note: '拖刺激强度看膜电位变化：静息电位、去极化、复极化、不应期——对应教材"兴奋的传导"' },
    { key: 'diffusion', name: '扩散（分子热运动）', subject: '生物',
      note: '分子从高浓度往低浓度扩散：对应渗透作用、自由扩散、质壁分离那几节' },
    { key: 'natural-selection', name: '自然选择（种群基因频率）', subject: '生物',
      note: '改环境与突变率，看几代之后种群性状和基因频率怎么变——对应"自然选择对基因频率的影响"' },
    { key: 'gene-expression-essentials', name: '基因表达基础（转录/翻译）', subject: '生物',
      note: 'DNA 转录成 mRNA、核糖体翻译成蛋白质：对应"转录""翻译"两节' },
    { key: 'molecule-shapes', name: '分子形状', subject: '化学',
      note: 'VSEPR：改电子对/成键数，看分子是直线、平面三角还是四面体' },
    { key: 'ph-scale', name: 'pH 酸碱', subject: '化学',
      note: '倒水、加酸加碱，看 pH 和颜色变化（配化学实验台一起用）' }
  ];

  /* 「生物」按钮进来时默认选哪一个：第一个生物模拟 */
  function firstOf(subject) {
    for (var index = 0; index < SIMS.length; index++) {
      if (SIMS[index].subject === subject) return SIMS[index].key;
    }
    return SIMS[0].key;
  }

  function $(selector) { return document.querySelector(selector); }

  function current() {
    var pick = $('#phetPick');
    var key = pick ? pick.value : SIMS[0].key;
    for (var index = 0; index < SIMS.length; index++) {
      if (SIMS[index].key === key) return SIMS[index];
    }
    return SIMS[0];
  }

  function load() {
    var sim = current();
    var frame = $('#phetFrame');
    var note = $('#phetNote');
    if (!frame) return;
    frame.src = 'vendor/phet/' + sim.key + '.html';
    frame.title = sim.name;
    if (note) note.textContent = sim.name + '：' + sim.note;
  }

  function open(subject) {
    var dialog = $('#phetDialog');
    if (!dialog) return;
    if (subject) {
      var pick = $('#phetPick');
      if (pick) pick.value = firstOf(subject);
    }
    if (!dialog.open) dialog.showModal();
    load();                                  // 每次打开都按当前选中的那个装
  }

  function bind() {
    var pick = $('#phetPick');
    if (pick) {
      pick.innerHTML = SIMS.map(function (sim) {
        return '<option value="' + sim.key + '">[' + sim.subject + '] ' + sim.name +
          '</option>';
      }).join('');
      pick.onchange = load;
    }
    var button = $('#btnPhet');
    if (button) button.onclick = open;
    var open2 = $('#phetOpen');               // 对话框里的「在新标签打开」（要投影/全屏时方便）
    if (open2) {
      open2.onclick = function () {
        window.open('vendor/phet/' + current().key + '.html', '_blank');
      };
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  window.W7Phet = { open: open, load: load, sims: SIMS };
})();
