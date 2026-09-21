// Usage: node scripts/build-brand.cjs [path-to-sharp]
// Only packages/resizes the approved artwork; does not redraw it.
const sharp = require(process.argv[2] || 'sharp');
const fs = require('node:fs/promises');
const path = require('node:path');
const dir = path.resolve(__dirname, '../public/brand');
const navy = '#1b365d';
async function main() {
  const master = path.join(dir, 'mark-master.png');
  for (const [name, size] of [['favicon-16',16],['favicon-32',32],['icon-192',192],['icon-512',512],['apple-touch-icon',180]]) {
    await sharp(master).resize(size,size).png().toFile(path.join(dir, `${name}.png`));
  }
  // Extra safe area keeps the complete mark inside circular launcher masks.
  const inset = await sharp(master).resize(360,360).png().toBuffer();
  await sharp({create:{width:512,height:512,channels:4,background:navy}})
    .composite([{input:inset,left:76,top:76}]).png().toFile(path.join(dir,'icon-maskable-512.png'));
  // ICO directory with PNG entries at the three conventional Windows sizes.
  const sizes=[16,32,48];
  const frames=await Promise.all(sizes.map(s=>sharp(master).resize(s,s).png().toBuffer()));
  const header=Buffer.alloc(6+16*frames.length);
  header.writeUInt16LE(1,2); header.writeUInt16LE(frames.length,4);
  let offset=header.length;
  frames.forEach((frame,i)=>{
    const p=6+i*16; header[p]=sizes[i]; header[p+1]=sizes[i];
    header.writeUInt16LE(1,p+4); header.writeUInt16LE(32,p+6);
    header.writeUInt32LE(frame.length,p+8); header.writeUInt32LE(offset,p+12);
    offset+=frame.length;
  });
  await fs.writeFile(path.join(dir,'favicon.ico'),Buffer.concat([header,...frames]));
  await fs.copyFile(path.join(dir,'favicon.ico'),path.join(dir,'../favicon.ico'));
  const mark=(await sharp(master).resize(256,256).png().toBuffer()).toString('base64');
  for (const [name,ink] of [['logo',navy],['logo-light','#ffffff']]) {
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="920" height="256" viewBox="0 0 920 256" role="img" aria-label="Brisavia"><image width="256" height="256" href="data:image/png;base64,${mark}"/><text x="292" y="166" font-family="Arial, sans-serif" font-size="112" font-weight="700" letter-spacing="-3" fill="${ink}">Brisavia</text></svg>`;
    await fs.writeFile(path.join(dir,`${name}.svg`),svg);
    await sharp(Buffer.from(svg)).png().toFile(path.join(dir,`${name}.png`));
  }
  console.log('Brand assets generated.');
}
main().catch(error=>{console.error(error);process.exitCode=1});
