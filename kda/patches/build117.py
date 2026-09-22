import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.6.zip')
work=pathlib.Path('/tmp/kda117')
if work.exists(): shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z: z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text().replace('Version: 1.1.6','Version: 1.1.7').replace("define('KDA_VERSION', '1.1.6');","define('KDA_VERSION', '1.1.7');")
s=s.replace("require_once KDA_DIR . 'includes/class-kda-repair.php';\n",
            "require_once KDA_DIR . 'includes/class-kda-repair.php';\nrequire_once KDA_DIR . 'includes/class-kda-repair-admin.php';\n")
main.write_text(s)

shutil.copy2('kda/patches/class-kda-repair-admin.php', root/'includes/class-kda-repair-admin.php')
readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.6','Stable tag: 1.1.7')
r += "\n= 1.1.7 =\n* Added Tools > KDA Repair admin page with Apply and Rollback buttons.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.7.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.7.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
