const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/companion.js'), 'utf8');
function rect(left, top, width, height) { return {left, top, right:left+width, bottom:top+height, width, height}; }
function overlaps(a,b) { return a.left<b.right && a.right>b.left && a.top<b.bottom && a.bottom>b.top; }
function fixture(viewport, empty) {
  const size=viewport<=720?56:64;
  const contentWidth=Math.min(viewport-24,empty?680:760);
  const contentLeft=(viewport-contentWidth)/2;
  const shell=rect(0,64,viewport,736);
  const column=rect(contentLeft,64,contentWidth,500);
  const lane=rect(contentLeft,600,contentWidth,size+24);
  const form=rect(contentLeft,600,contentWidth,200);
  const input=rect(contentLeft,lane.bottom,contentWidth,64);
  const context={innerWidth:viewport,document:{getElementById(id){return id==='sage-companion-toggle'?{getBoundingClientRect:()=>rect(0,0,size,size)}:null;}}};
  vm.createContext(context); vm.runInContext(code,context);
  const chatShell={dataset:{},getBoundingClientRect:()=>shell,querySelector:s=>({getBoundingClientRect:()=>s==='.composer-inner'?column:lane})};
  const controller=context.SageCompanion.create({chatShell,chatForm:{getBoundingClientRect:()=>form}});
  return {controller,chatShell,column,lane,input,size};
}
for (const width of [1600,1280,1024,800,720,390,320]) {
  for (const empty of [true,false]) test(`safe region ${width}px ${empty?'welcome':'conversation'}`,()=>{
    const {controller,chatShell,column,lane,input,size}=fixture(width,empty);
    const b=controller.movementBounds();
    // Clamping arbitrary saved/dragged positions must keep the entire mascot
    // outside both the reading column and composer actions. The same convex
    // region also keeps every intermediate wandering position safe.
    for (const x of [-1000,b.minX,(b.minX+b.maxX)/2,b.maxX,10000]) {
      for(const y of [-1000,b.minY,(b.minY+b.maxY)/2,b.maxY,10000]) {
        const p=controller.clampPosition(x,y), mascot=rect(p.left,p.top,size,size);
        assert.equal(overlaps(mascot,column),false);
        assert.equal(overlaps(mascot,input),false);
        assert.ok(mascot.left>=0&&mascot.right<=width);
        if(chatShell.dataset.companionPlacement==='dock') {
          assert.ok(mascot.top>=lane.top&&mascot.bottom<=lane.bottom);
        }
      }
    }
  });
}
