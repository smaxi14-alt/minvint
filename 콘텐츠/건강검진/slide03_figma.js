
await figma.loadFontAsync({ family: "Noto Sans KR", style: "Regular" });
await figma.loadFontAsync({ family: "Noto Sans KR", style: "Bold" });
const frame = figma.getNodeById("FRAME_ID");
const created = [];

  const t0 = figma.createText();
  frame.appendChild(t0);
  t0.characters = `증권 진단 전 vs 후`;
  t0.fontSize = 32;
  t0.fills = [{ type: "SOLID", color: { r: 0.102, g: 0.184, b: 0.369 } }];
  t0.fontName = { family: "Noto Sans KR", style: "Bold" };
  t0.textAlignHorizontal = "CENTER";
  t0.resize(900, 70);
  t0.x = 90; t0.y = 60;
  created.push(t0.id);
  const t1 = figma.createText();
  frame.appendChild(t1);
  t1.characters = `BEFORE
경고 수치 방치
보험 미확인 상태
중증 발생 시 자기부담
3,000만원↑`;
  t1.fontSize = 22;
  t1.fills = [{ type: "SOLID", color: { r: 0.753, g: 0.224, b: 0.169 } }];
  t1.fontName = { family: "Noto Sans KR", style: "Bold" };
  t1.textAlignHorizontal = "CENTER";
  t1.resize(400, 320);
  t1.x = 90; t1.y = 200;
  created.push(t1.id);
  const t2 = figma.createText();
  frame.appendChild(t2);
  t2.characters = `AFTER
증권 진단 후 보장 최적화
실손·중대질병 공백 해소
동일 상황 자기부담
300만원 이하`;
  t2.fontSize = 22;
  t2.fills = [{ type: "SOLID", color: { r: 0.153, g: 0.682, b: 0.376 } }];
  t2.fontName = { family: "Noto Sans KR", style: "Bold" };
  t2.textAlignHorizontal = "CENTER";
  t2.resize(400, 320);
  t2.x = 590; t2.y = 200;
  created.push(t2.id);
  const t3 = figma.createText();
  frame.appendChild(t3);
  t3.characters = `차이: 2,700만원`;
  t3.fontSize = 72;
  t3.fills = [{ type: "SOLID", color: { r: 0.878, g: 0.486, b: 0.227 } }];
  t3.fontName = { family: "Noto Sans KR", style: "Bold" };
  t3.textAlignHorizontal = "CENTER";
  t3.resize(900, 120);
  t3.x = 90; t3.y = 780;
  created.push(t3.id);
  const t4 = figma.createText();
  frame.appendChild(t4);
  t4.characters = `* 가입 상품 및 조건에 따라 상이`;
  t4.fontSize = 16;
  t4.fills = [{ type: "SOLID", color: { r: 0.6, g: 0.6, b: 0.6 } }];
  t4.fontName = { family: "Noto Sans KR", style: "Regular" };
  t4.textAlignHorizontal = "CENTER";
  t4.resize(900, 40);
  t4.x = 90; t4.y = 1010;
  created.push(t4.id);
return { createdNodeIds: created };
