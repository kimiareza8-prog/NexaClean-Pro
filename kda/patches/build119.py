import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.8.zip')
work=pathlib.Path('/tmp/kda119')
if work.exists(): shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z: z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text()
s=s.replace('Version: 1.1.8','Version: 1.1.9')
s=s.replace("define('KDA_VERSION', '1.1.8');","define('KDA_VERSION', '1.1.9');")
s=s.replace("require_once KDA_DIR . 'includes/class-kda-repair-inspector.php';\n",
            "require_once KDA_DIR . 'includes/class-kda-repair-inspector.php';\nrequire_once KDA_DIR . 'includes/class-kda-agent-card.php';\n")
marker="""        register_rest_route('kda/v1', '/ping', array(
"""
addition="""        register_rest_route('kda/v1', '/agent', array(
            'methods' => WP_REST_Server::READABLE,
            'callback' => array($this, 'rest_agent'),
            'permission_callback' => array('KDA_Security', 'authorize_diagnostic_request'),
        ));

"""+marker
if marker not in s:
    raise SystemExit('ping route marker missing')
s=s.replace(marker,addition,1)

marker2="    public function rest_ping() {\n"
repl2="    public function rest_agent() {\n        return $this->no_store(KDA_Agent_Card::data());\n    }\n\n"+marker2
if marker2 not in s:
    raise SystemExit('rest_ping marker missing')
s=s.replace(marker2,repl2,1)
main.write_text(s)

shutil.copy2('kda/patches/class-kda-agent-card.php', root/'includes/class-kda-agent-card.php')

readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.8','Stable tag: 1.1.9')
r += "\n= 1.1.9 =\n* Added token-authenticated /agent machine-readable usage guide so a new AI assistant can discover the read-only diagnostic workflow without prior chat context.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.9.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.9.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
