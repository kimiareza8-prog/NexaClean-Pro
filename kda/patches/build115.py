import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.4.zip')
work=pathlib.Path('/tmp/kda115')
if work.exists():
    shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z:
    z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text()
s=s.replace('Version: 1.1.4','Version: 1.1.5')
s=s.replace("define('KDA_VERSION', '1.1.4');","define('KDA_VERSION', '1.1.5');")
s=s.replace("require_once KDA_DIR . 'includes/class-kda-profiler.php';\n",
            "require_once KDA_DIR . 'includes/class-kda-profiler.php';\nrequire_once KDA_DIR . 'includes/class-kda-repair-inspector.php';\n")
marker="""        register_rest_route('kda/v1', '/signed-profile', array(
            'methods' => WP_REST_Server::READABLE,
            'callback' => array($this, 'rest_profile'),
            'permission_callback' => array('KDA_Security', 'authorize_signed_profile_request'),
        ));
"""
addition=marker+"""        register_rest_route('kda/v1', '/signed-repair-inspect', array(
            'methods' => WP_REST_Server::READABLE,
            'callback' => array($this, 'rest_repair_inspect'),
            'permission_callback' => array('KDA_Security', 'authorize_signed_profile_request'),
        ));
"""
if marker not in s:
    raise SystemExit('signed-profile route marker missing')
s=s.replace(marker,addition,1)
marker2="    public function rest_errors() {\n"
repl2="    public function rest_repair_inspect() {\n        return $this->no_store(KDA_Repair_Inspector::inspect());\n    }\n\n"+marker2
if marker2 not in s:
    raise SystemExit('rest_errors marker missing')
s=s.replace(marker2,repl2,1)
main.write_text(s)

shutil.copy2('kda/patches/class-kda-repair-inspector.php', root/'includes/class-kda-repair-inspector.php')

readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.4','Stable tag: 1.1.5')
r += "\n= 1.1.5 =\n* Added signed read-only repair inspector for Elementor/JetMenu configuration discovery.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.5.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.5.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
