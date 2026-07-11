#!/bin/bash
# Test de bout en bout : login -> classe mock -> sujet -> génération -> lot de
# scan mock -> revue -> finalisation -> overlay -> suivi élève -> coûts.
set -e
API=http://localhost:8787/api
J="curl -s -H Content-Type:application/json"

TOKEN=$($J -X POST $API/auth/login -d '{"email":"prof@mathprint.local","password":"mathprint"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
A() { curl -s -H "Authorization: Bearer $TOKEN" "$@"; }
AJ() { curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"; }

echo "== classes =="
CLASS_ID=$(A $API/classes | python3 -c "import sys,json;d=json.load(sys.stdin);print(d[0]['id']);print(d, file=sys.stderr)")

echo "== création sujet =="
ASSESS_ID=$(AJ -X POST $API/assessments -d "{\"class_id\":\"$CLASS_ID\",\"type\":\"control\",\"title\":\"Contrôle relatifs et fractions\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

echo "== proposition automatique =="
EX_IDS=$(A $API/assessments/$ASSESS_ID/suggestion | python3 -c "import sys,json;d=json.load(sys.stdin);print(json.dumps(d['exercise_ids']));print(d['reason'],file=sys.stderr)")

echo "== génération PDF =="
AJ -X POST $API/assessments/$ASSESS_ID/generate -d "{\"exercise_ids\":$EX_IDS}"
echo

echo "== lot de scan =="
BATCH_ID=$(A -X POST "$API/scans/batches?assessment_id=$ASSESS_ID" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
sleep 3
A $API/scans/batches/$BATCH_ID | python3 -m json.tool | head -20

echo "== file de revue =="
A $API/scans/batches/$BATCH_ID/reviews | python3 -c "
import sys, json
rs = json.load(sys.stdin)
print(f'{len(rs)} revue(s) en attente')
for r in rs[:3]:
    print(' -', r['category'], '|', r['statement'][:50], '| OCR:', r['ocr_text'], '| attendu:', r['expected'])
print(json.dumps([r['review_id'] for r in rs]))" > /tmp/reviews.txt
cat /tmp/reviews.txt
REVIEW_IDS=$(tail -1 /tmp/reviews.txt)

echo "== résolution des revues (accepter) =="
python3 - "$TOKEN" "$REVIEW_IDS" <<'EOF'
import sys, json, urllib.request
token, ids = sys.argv[1], json.loads(sys.argv[2])
for rid in ids:
    req = urllib.request.Request(f"http://localhost:8787/api/scans/reviews/{rid}/resolve",
        data=json.dumps({"action": "accept"}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    print(rid[:8], urllib.request.urlopen(req).status)
EOF

echo "== finalisation =="
A -X POST $API/scans/batches/$BATCH_ID/finalize
echo
echo "== overlay =="
A -X POST $API/scans/batches/$BATCH_ID/overlays
echo
echo "== élève : compétences + oubli =="
STUDENT_ID=$(A $API/classes/$CLASS_ID/students | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")
A $API/students/$STUDENT_ID | python3 -m json.tool | head -40
echo "== recalcul niveau =="
A -X POST $API/students/$STUDENT_ID/level/recompute
echo
echo "== rapport Claude (mock) =="
A -X POST "$API/students/$STUDENT_ID/reports?period=juillet"
echo
echo "== coûts =="
A $API/costs
echo
echo "== dashboard =="
A $API/dashboard | python3 -m json.tool | head -25
echo "== fichiers générés =="
ls -la ../data/assessments/$ASSESS_ID/generated ../data/assessments/$ASSESS_ID/overlays 2>/dev/null
