import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.9.zip')
work=pathlib.Path('/tmp/kda110')
if work.exists(): shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z: z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text()
s=s.replace('Version: 1.1.9','Version: 1.1.10')
s=s.replace("define('KDA_VERSION', '1.1.9');","define('KDA_VERSION', '1.1.10');")
s=s.replace("require_once KDA_DIR . 'includes/class-kda-agent-card.php';\n",
            "require_once KDA_DIR . 'includes/class-kda-agent-card.php';\nrequire_once KDA_DIR . 'includes/class-kda-cache-inspector.php';\n")
old="""    public function rest_report() {
        return $this->no_store(KDA_Diagnostics::full_report());
    }
"""
new="""    public function rest_report() {
        $report = KDA_Diagnostics::full_report();
        $report['cache_layer'] = KDA_Cache_Inspector::inspect();
        return $this->no_store($report);
    }
"""
if old not in s:
    raise SystemExit('rest_report block missing')
s=s.replace(old,new,1)
main.write_text(s)

shutil.copy2('kda/patches/class-kda-cache-inspector.php', root/'includes/class-kda-cache-inspector.php')

readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.9','Stable tag: 1.1.10')
r += "\n= 1.1.10 =\n* Added read-only page-cache/drop-in inspector to diagnostics.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.10.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.10.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
