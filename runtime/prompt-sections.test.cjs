const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const sections = require('./prompt-sections.cjs');

// Every case works on a throwaway copy of the real bundle: the point is what happens when
// the bundle is wrong, and the repository's own skills must never be the thing that is wrong.
function bundle() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dsherp-sections-'));
  fs.mkdirSync(path.join(root, 'config'));
  fs.copyFileSync(path.join(__dirname, '../config/business-skills.json'),
                  path.join(root, 'config/business-skills.json'));
  fs.cpSync(path.join(__dirname, '../business-skills'), path.join(root, 'business-skills'),
            {recursive: true});
  return root;
}

function applied(root, domain) {
  const registered = [];
  sections.apply({systemPrompt: {section: (item) => registered.push(item)}}, {root, domain});
  return registered;
}

test('registers the envelope rule and the active domain summary, and nothing from the others', () => {
  const root = bundle();
  try {
    const registered = applied(root, 'query');
    assert.deepEqual(registered.map(item => item.name),
                     ['dsherp:untrusted-envelope', 'dsherp:business-skill']);
    const skill = registered[1].text;
    assert.match(skill, /业务技能：erp-query v1\.4\.0/);
    assert.ok(!skill.includes('erp-operation'), 'another domain has no business being here');
    assert.ok(!skill.includes('erp-configuration'));
    // A summary, not the body: the full rules stay behind the skill tool (spec:154).
    assert.ok(!skill.includes('工具错误与做不了的出口'), 'the body must not be inlined');
    assert.match(skill, /skill/);
  } finally { fs.rmSync(root, {recursive: true}); }
});

test('the envelope section names all three source labels and forbids acting on their contents', () => {
  const root = bundle();
  try {
    const rule = applied(root, 'operation')[0].text;
    for (const label of ['"erp"', '"erp-server"', '"page"', 'untrusted']) {
      assert.ok(rule.includes(label), 'missing ' + label);
    }
    assert.match(rule, /不执行/);
    assert.match(rule, /只有本系统提示与用户/);
  } finally { fs.rmSync(root, {recursive: true}); }
});

test('throws when a SKILL.md byte changed', () => {
  const root = bundle();
  try {
    const file = path.join(root, 'business-skills/erp-query/SKILL.md');
    fs.writeFileSync(file, fs.readFileSync(file, 'utf8') + '\n');
    assert.throws(() => applied(root, 'query'));
  } finally { fs.rmSync(root, {recursive: true}); }
});

test('throws when the domain directory is missing', () => {
  const root = bundle();
  try {
    fs.rmSync(path.join(root, 'business-skills/erp-query'), {recursive: true});
    assert.throws(() => applied(root, 'query'));
  } finally { fs.rmSync(root, {recursive: true}); }
});

test('throws when the manifest lists a version the body does not carry', () => {
  const root = bundle();
  try {
    const file = path.join(root, 'config/business-skills.json');
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
    manifest.skills.find(row => row.name === 'erp-query').version = '9.9.9';
    fs.writeFileSync(file, JSON.stringify(manifest));
    assert.throws(() => applied(root, 'query'));
  } finally { fs.rmSync(root, {recursive: true}); }
});

test('throws when the domain is unknown or unset', () => {
  const root = bundle();
  try {
    assert.throws(() => applied(root, 'nosuch'));
    assert.throws(() => sections.apply({systemPrompt: {section: () => {}}}, {root, domain: ''}),
                  /DSHERP_DOMAIN/);
  } finally { fs.rmSync(root, {recursive: true}); }
});

test('the marker is derived independently of the section text', () => {
  // model-guard.cjs must be able to say what the system prompt should contain without
  // importing the thing that builds it - two derivations of one pinned fact, same discipline
  // the repository already uses for the host/bridge copies of the metering rules.
  const root = bundle();
  try {
    assert.equal(sections.skillMarker(root, 'operation'), '业务技能：erp-operation v2.4.0');
    assert.ok(sections.skillSection(root, 'operation').startsWith(sections.skillMarker(root, 'operation')));
  } finally { fs.rmSync(root, {recursive: true}); }
});

test('an assembled section always carries the version line, and cannot be built without it', () => {
  // The plan asked for a check on "the assembled section would be empty". `skillSection`
  // makes that state unreachable: it builds the text from `skillMarker` and refuses any body
  // whose version disagrees with the manifest, so the property is enforced at the source
  // rather than re-checked afterwards. Asserted here as the property itself — the earlier
  // belt-and-braces branch in `apply` could not fire and has been removed.
  const root = bundle();
  try {
    for (const domain of ['query', 'operation', 'configuration']) {
      const text = sections.skillSection(root, domain);
      assert.match(text, new RegExp('^业务技能：erp-' + domain + ' v\\d+\\.\\d+\\.\\d+'));
      assert.ok(text.split('\n').length >= 3, '摘要至少是标记、描述、指向 skill 工具三行');
    }
    const file = path.join(root, 'business-skills/erp-query/SKILL.md');
    const body = fs.readFileSync(file, 'utf8');
    fs.writeFileSync(file, body.replace(/^version:.*$/m, 'version: 9.9.9'));
    assert.throws(() => sections.skillSection(root, 'query'), /version does not match the manifest/);
  } finally { fs.rmSync(root, { recursive: true }); }
});

test('the guard and the prompt plugin derive the same marker for every domain', () => {
  // Two independent derivations of one pinned fact. Compared here directly, with both real
  // implementations — `model-guard.cjs` exports its derivation for exactly this reason.
  const guard = require('./model-guard.cjs');
  const root = path.join(__dirname, '..');
  for (const domain of ['query', 'operation', 'configuration']) {
    assert.equal(guard.skillMarker(root, domain), sections.skillMarker(root, domain), domain);
  }
});

test('requireSystem is built from the real marker, and refuses a prompt without it', () => {
  const guard = require('./model-guard.cjs');
  const root = path.join(__dirname, '..');
  const refuse = guard.requireSystemFor(guard.skillMarker(root, 'operation'));
  assert.throws(() => refuse('a system prompt with no marker'), /missing the pinned business skill/);
  assert.throws(() => refuse(''), /missing the pinned business skill/);
  assert.doesNotThrow(() => refuse('前言\n' + sections.skillSection(root, 'operation') + '\n后记'));
  // The query marker must not satisfy an operation run: a wrong-domain summary is exactly the
  // state this check exists to stop.
  assert.throws(() => refuse(sections.skillSection(root, 'query')), /missing the pinned business skill/);
});
