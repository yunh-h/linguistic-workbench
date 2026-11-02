const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const context = { console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../export.js"), "utf8"), context);

const field = value => ({ status: "present", value, source_ids: ["parser"] });
const analysis = {
  input: { language: "fr", text: "au feu. Salut." }, status: "complete",
  sentences: [
    { id: "s1", span: { start: 0, end: 7 },
      segments: [{surface: "au", word_ids: ["w1", "w2"]}, {surface: "feu", word_ids: ["w3"]}],
      words: [
        {id:"w1", form:"à", lemma:field("à"), upos:field("ADP"), morphology:field({}), dependency:field(null), extensions:{}},
        {id:"w2", form:"le", lemma:field("le"), upos:field("DET"), morphology:field({}), dependency:field(null), extensions:{}},
        {id:"w3", form:"feu", lemma:field("feu"), upos:field("NOUN"), morphology:field({Number:["Sing"]}), dependency:field({relation:"root",head_word_id:null}), extensions:{}},
      ]},
    { id: "s2", span: {start:8,end:14}, segments:[{surface:"Salut",word_ids:["w4"]}],
      words:[{id:"w4",form:"Salut",lemma:field("salut"),upos:field("INTJ"),morphology:field({}),dependency:field(null),extensions:{}}]},
  ],
  annotations: [
    {kind:"translation",sentence_id:"s1",word_ids:[],text:field("火")},
    {kind:"translation",sentence_id:"s2",word_ids:[],text:field("你好")},
    {kind:"lexical_gloss",sentence_id:"s1",word_ids:["w3"],text:field("traffic light")},
  ],
  sources:[{id:"parser",kind:"parser",name:"Stanza",version:"1"}], warnings:[], disagreements:[],
};
context.analysis = analysis;
const result = vm.runInContext('markdownDocument([{analysis, translations:new Map([["0:7","紅綠燈 <script>"]])}], true)', context);
assert.match(result, /翻譯（使用者）：\*\* 紅綠燈 &lt;script&gt;/);
assert.doesNotMatch(result, /翻譯（模型）：\*\* 火/);
assert.match(result, /翻譯（模型）：\*\* 你好/);
assert.match(result, /\| au \| à \|/);
assert.match(result, /\| au \| le \|/);
assert.match(result, /詳細語法分析/);
assert.match(result, /traffic light/);
console.log("Markdown export checks passed");
