const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2] || require('node:path').join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const implementation = source.slice(source.indexOf('async function apiRequest('), source.indexOf('async function apiStreamRequest('));
const never = () => new Promise(() => {});
async function run() {
  let response = {ok:true, json:async()=>({answer:'ok'})};
  let origin = async()=> 'https://example.test';
  let timers = 0;
  const sandbox = {Headers, FormData, AbortController, DOMException,
    setTimeout: (...args)=>{timers++;return setTimeout(...args);},
    clearTimeout: timer=>{timers--;clearTimeout(timer);},
    resolveApiOrigin: ()=>origin(), buildApiUrl: (path, base)=>base+path,
    friendlyHttpErrorMessage: (status,text)=>`${status}: ${text}`,
    fetch: async()=>response};
  vm.runInNewContext(implementation,sandbox);
  const call = (options={}) => sandbox.apiRequest('/chat',{timeoutMs:25,...options});
  assert.equal((await call()).answer,'ok');
  response = {ok:true,json:never};
  await assert.rejects(call(), /请求超时/);
  response = {ok:false,status:503,text:never};
  await assert.rejects(call(), /请求超时/);
  origin=never;
  await assert.rejects(call(), /请求超时/);
  origin=async()=> 'https://example.test';
  response={ok:true,json:never};
  const controller=new AbortController();
  const pending=call({abortController:controller});
  controller.abort('user-cancelled');
  await assert.rejects(pending,error=>error.cancelled === true);
  response={ok:false,status:429,text:async()=> 'busy',headers:new Headers({'Retry-After':'3','X-Queue-Position':'2'})};
  await assert.rejects(call(),error=>error.status===429 && error.retryAfterSeconds===3 && error.queuePosition===2);
  response={ok:true,json:async()=>({answer:'recovered'})};
  assert.equal((await call()).answer,'recovered');
  assert.equal(timers,0);
  console.log('API full-response timeout, cancellation, HTTP metadata and recovery passed');
}
// Unsettled Promises alone do not keep Node alive: fail explicitly rather than
// allowing the old hanging implementation to exit with a false green result.
const watchdog = setTimeout(()=>{console.error('API call never settled');process.exitCode=1;},1000);
run().then(()=>clearTimeout(watchdog),error=>{clearTimeout(watchdog);console.error(error);process.exitCode=1;});
