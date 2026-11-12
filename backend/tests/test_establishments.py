"""Parcours d'exploitation multi-établissements, via les vrais droits HTTP."""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from io import BytesIO
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base, get_db
from app.models import *
from app.routers import auth, operations, org, assessments, students, content, printing, grades, misc, users_admin
from app.services.education import seed_structure
from app.services.security import make_token


@pytest.fixture
def env():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as db:
        seed_structure(db)
        admin = User(email='admin@test.fr', password_hash='x', role='admin')
        corrector = User(email='corrector@test.fr', password_hash='x', role='corrector')
        year = SchoolYear(label='2026-2027', active=True)
        db.add_all([admin, corrector, year]); db.commit()
        tokens = {u.role: {'Authorization': 'Bearer ' + make_token(u)} for u in (admin, corrector)}
        year_id = year.id
    app = FastAPI()
    for mod in (auth, operations, org, assessments, students, content, printing, grades, misc, users_admin): app.include_router(mod.router)
    def get_session():
        with factory() as db: yield db
    app.dependency_overrides[get_db] = get_session
    client = TestClient(app)
    return client, factory, tokens, year_id


def create_teacher_class(env, name='Camille', program='fr'):
    client, factory, tokens, year = env
    h = tokens['admin']
    school = client.post('/api/operations/schools', headers=h, json={
        'name': 'École '+name, 'country_id': 'CI', 'program_ids': ['fr', 'ci']})
    assert school.status_code == 200, school.text
    sid = school.json()['id']
    teacher = client.post('/api/operations/teachers', headers=h, json={
        'name': name, 'school_ids': [sid], 'service_mode': 'accompanied'})
    assert teacher.status_code == 200, teacher.text
    tid = teacher.json()['id']
    inv = client.post(f'/api/operations/teachers/{tid}/invitation', headers=h)
    assert inv.status_code == 200, inv.text
    token = inv.json()['path'].split('invitation=')[1]
    signup = client.post('/api/auth/register', json={'token': token, 'email': name.lower()+'@test.fr', 'password': 'Long-password-123'})
    assert signup.status_code == 200, signup.text
    th = {'Authorization': 'Bearer '+signup.json()['token']}
    cls = client.post('/api/classes', headers=h, json={'name': '3A', 'grade_level': '3e',
        'school_year_id': year, 'school_id': sid, 'program_id': program,
        'teacher_profile_id': tid, 'students_text': 'Alice\nBenoît'})
    assert cls.status_code == 200, cls.text
    return sid, tid, cls.json()['id'], th, token


def test_invitation_private_and_single_use_and_cross_school_isolation(env):
    client, factory, tokens, year = env
    s1, t1, c1, h1, token = create_teacher_class(env)
    s2, t2, c2, h2, _ = create_teacher_class(env, 'Paul')
    assert client.post('/api/auth/register', json={'token': token, 'email': 'other@test.fr', 'password': 'Long-password-123'}).status_code == 410
    assert client.get('/api/operations/teachers', headers=h1).status_code == 403
    assert [r['id'] for r in client.get('/api/operations/schools', headers=h1).json()] == [s1]
    own = client.get('/api/classes', headers=h1)
    assert own.status_code == 200, own.text
    assert [r['id'] for r in own.json()] == [c1]
    assert client.get(f'/api/classes/{c2}/students', headers=h1).status_code == 404
    assert client.patch(f'/api/classes/{c2}', headers=h1, json={'name':'hacked'}).status_code == 404
    assert client.post('/api/assessments', headers=h1, json={'class_id':c2, 'title':'hacked'}).status_code == 404
    student = client.get(f'/api/classes/{c1}/students', headers=h1).json()[0]
    assert client.get(f'/api/students/{student["id"]}', headers=h2).status_code == 404
    assert client.get('/api/operations/dashboard', headers=h1).status_code == 403
    # Un professeur accompagné peut aussi gérer ses propres classes.
    new = client.post('/api/classes', headers=h1, json={'school_id':s1, 'school_year_id':year,
        'name':'4A', 'grade_level':'4e', 'program_id':'ci'})
    assert new.status_code == 200, new.text


def test_corrector_bank_immutable_and_program_separation(env):
    client, factory, tokens, year = env
    sid, tid, cid, th, _ = create_teacher_class(env, program='ci')
    matrix = client.get(f'/api/assessments/competency-matrix?grade_level=3e&class_id={cid}', headers=th)
    assert matrix.status_code == 200, matrix.text
    assert matrix.json()['domains'] == []
    made = client.post('/api/assessments', headers=th, json={'class_id':cid, 'title':'Test'})
    assert made.status_code == 200, made.text
    aid = made.json()['id']
    with factory() as db:
        fw = db.query(CompetencyFramework).filter_by(program_id='fr', grade_level='3e').first()
        comp = Competency(framework_id=fw.id, code='A', label='Calcul')
        db.add(comp); db.commit(); comp_id = comp.id
    assert client.patch(f'/api/assessments/{aid}', headers=th, json={'competency_ids':[comp_id]}).status_code == 422
    assert client.post('/api/content/exercises/generate', headers=tokens['corrector'], json={'competency_id':comp_id}).status_code == 403
    assert client.post('/api/assessments/exercises/ai-prepare', headers=tokens['corrector'], json={'competency_id':comp_id}).status_code == 403
    # Suspension stoppe la production, sans supprimer les résultats.
    school = next(s for s in client.get('/api/operations/schools', headers=tokens['corrector']).json() if s['id'] == sid)
    school['service_active'] = False
    # L'Atelier lit les établissements mais ne les modifie pas : c'est l'admin.
    assert client.put(f'/api/operations/schools/{sid}', headers=tokens['corrector'], json=school).status_code == 403
    assert client.put(f'/api/operations/schools/{sid}', headers=tokens['admin'], json=school).status_code == 200
    assert client.post('/api/assessments', headers=th, json={'class_id':cid, 'title':'Suspendu'}).status_code == 409
    assert client.post('/api/printers/print', headers=th, json={'assessment_id':aid,'file':'subject_batch.pdf','printer':'X'}).status_code == 409


def test_school_year_rollover_and_subject_levels(env):
    client, factory, tokens, year = env
    sid, tid, cid, th, _ = create_teacher_class(env)
    student = client.get(f'/api/classes/{cid}/students', headers=th).json()[0]
    assert client.post(f'/api/students/{student["id"]}/level', headers=th, json={'level':8, 'locked':True}).status_code == 200
    assert client.get(f'/api/students/{student["id"]}', headers=th).json()['level'] == 8
    with factory() as db:
        db.add(Subject(id='physics', name='Physique'))
        db.add(ClassTeaching(class_id=cid, subject_id='physics', teacher_id=tid)); db.commit()
    assert client.get(f'/api/students/{student["id"]}?subject_id=physics', headers=th).json()['level'] is None
    assert client.post('/api/operations/years', headers=tokens['corrector'], json={'label':'2027-2028'}).status_code == 403
    year2 = client.post('/api/operations/years', headers=tokens['admin'], json={'label':'2027-2028'}).json()['id']
    assert client.post(f'/api/operations/classes/{cid}/rollover', headers=tokens['corrector'], json={'year_id':year2,'name':'2A','grade_level':'2nde'}).status_code == 403
    result = client.post(f'/api/operations/classes/{cid}/rollover', headers=tokens['admin'], json={'year_id':year2,'name':'2A','grade_level':'2nde'})
    assert result.status_code == 200, result.text
    new_students = client.get(f'/api/classes/{result.json()["id"]}/students', headers=th).json()
    assert len(new_students) == 2 and all(s['level'] is None for s in new_students)
    assert client.post(f'/api/operations/years/{year}/archive', headers=tokens['corrector']).status_code == 403
    assert client.post(f'/api/operations/years/{year}/archive', headers=tokens['admin']).status_code == 200
    assert cid not in {c['id'] for c in client.get('/api/classes', headers=th).json()}
    assert client.get(f'/api/students/{student["id"]}', headers=th).status_code == 200


def test_delivery_sheet_duplex_and_physical_slot(env, tmp_path):
    from app.services.delivery_sheet import render_delivery_sheet, with_delivery_sheet
    from app.routers.printing import _reverse_pdf_pages
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    client, factory, tokens, year = env
    sid, tid, cid, th, _ = create_teacher_class(env)
    aid = client.post('/api/assessments', headers=th, json={'class_id':cid,'title':'Fractions et équations'}).json()['id']
    with factory() as db:
        sheet = render_delivery_sheet(db, db.get(Assessment, aid), printed_at=datetime(2026,10,26,10,tzinfo=timezone.utc))
    assert len(PdfReader(BytesIO(sheet)).pages) == 1
    assert '26/10/2026' in PdfReader(BytesIO(sheet)).pages[0].extract_text()
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=A4)
    for text in ('RECTO','VERSO'): c.drawString(30,700,text);c.showPage()
    c.save()
    combined = with_delivery_sheet(buf.getvalue(), sheet, duplex=True)
    pages = PdfReader(BytesIO(combined)).pages
    assert len(pages)==4 and 'LIVRAISON' in pages[0].extract_text() and not pages[1].extract_text()
    assert 'RECTO' in pages[2].extract_text() and 'VERSO' in pages[3].extract_text()
    # Le verso de l'intercalaire accompagne la seconde passe sans décalage.
    verso = with_delivery_sheet(buf.getvalue(),sheet,pass_side='verso')
    assert not PdfReader(BytesIO(verso)).pages[0].extract_text()
    (tmp_path/'intercalaire.pdf').write_bytes(sheet)


def test_subject_permissions_are_per_class_not_union_of_teacher_subjects(env):
    client, factory, tokens, year = env
    s1, t1, c1, h1, _ = create_teacher_class(env)
    s2, t2, c2, h2, _ = create_teacher_class(env, 'Paul')
    assert client.post('/api/operations/subjects', headers=tokens['admin'], json={'id':'physics','name':'Physique'}).status_code == 200
    with factory() as db:
        db.add(TeacherSchool(teacher_id=t1, school_id=s2))
        db.add(ClassTeaching(class_id=c2, subject_id='physics', teacher_id=t1))
        st = db.query(Student).filter_by(class_id=c2).first()
        db.add(StudentLevel(student_id=st.id, subject_id='math', level=9))
        db.add(StudentLevel(student_id=st.id, subject_id='physics', level=3))
        student_id = st.id
        db.commit()
    assert client.get(f'/api/students/{student_id}?subject_id=math', headers=h1).status_code == 404
    assert client.post(f'/api/students/{student_id}/level?subject_id=math', headers=h1, json={'level':1,'locked':True}).status_code == 404
    assert client.get(f'/api/students/{student_id}?subject_id=physics', headers=h1).json()['level'] == 3
    result = client.post('/api/assessments', headers=h1, json={'class_id':c2,'subject_id':'physics','title':'Physique'})
    assert result.status_code == 200, result.text
    aid = result.json()['id']
    assert client.get(f'/api/assessments/{aid}/copies', headers=h2).status_code == 404
    # L'ancienne génération de contenus ne peut pas contourner la banque.
    assert client.post(f'/api/assessments/{aid}/generate', headers=h1, json={}).status_code == 403


def test_group_uses_own_framework_and_student_list(env):
    from app.services.education import assessment_students
    client, factory, tokens, year = env
    sid, tid, cid, th, _ = create_teacher_class(env)
    assert client.patch(f'/api/classes/{cid}', headers=th, json={'grade_level':'tle'}).status_code == 200
    teaching = client.get(f'/api/operations/classes/{cid}/teachings',headers=th).json()[0]
    students = client.get(f'/api/classes/{cid}/students',headers=th).json()
    r=client.post('/api/operations/groups',headers=th,json={'teaching_id':teaching['id'],'path_id':'fr-expertes','name':'Maths expertes','student_ids':[students[0]['id']]})
    assert r.status_code == 200, r.text
    gid=r.json()['id']
    made=client.post('/api/assessments',headers=th,json={'class_id':cid,'group_id':gid,'title':'Maths expertes'})
    assert made.status_code == 200,made.text
    with factory() as db:
        a=db.get(Assessment,made.json()['id'])
        assert db.get(CompetencyFramework,a.framework_id).path_id == 'fr-expertes'
        assert [s.id for s in assessment_students(db,a)] == [students[0]['id']]
    eligibility=client.get(f'/api/assessments/auto-eligibility?class_id={cid}&group_id={gid}',headers=th)
    assert eligibility.status_code == 200,eligibility.text
    assert eligibility.json()['students'] == 1


def test_blank_copy_requires_complete_confident_readings_and_keeps_scan_slot(env):
    from app.services.pipeline import copy_is_blank, copy_overlay_pages
    client, factory, tokens, year = env
    _, _, cid, th, _ = create_teacher_class(env)
    aid=client.post('/api/assessments',headers=th,json={'class_id':cid,'title':'Test'}).json()['id']
    with factory() as db:
        student=db.query(Student).filter_by(class_id=cid).first()
        copy=Copy(assessment_id=aid,student_id=student.id,total_pages=2)
        db.add(copy);db.flush()
        page=DocumentPage(copy_id=copy.id,page_no=1)
        page2=DocumentPage(copy_id=copy.id,page_no=2)
        item=CopyItem(copy_id=copy.id,sequence=1,response_type='qcm_single',catalog_id='test',statement='Question',correction='A')
        db.add_all([page,page2,item]);db.flush()
        response=StudentResponse(copy_item_id=item.id,zone_id='test-zone',selected_choices=[])
        db.add(response);db.commit()
        assert not copy_is_blank(db,copy)
        reading=OcrAttempt(zone_id='test-zone',provider='cv_local',confidence=1.0)
        db.add(reading);db.commit()
        assert copy_is_blank(db,copy)
        reading.confidence=0.1;db.commit()
        assert not copy_is_blank(db,copy)
        reading.confidence=1;response.selected_choices=[0];db.commit()
        assert not copy_is_blank(db,copy)
        response.selected_choices=[];copy.status='absent';db.commit()
        overlay=copy_overlay_pages(db,db.get(Assessment,aid),copy,{page.id:8,page2.id:9},persist=False)
        assert [p['_scan_index'] for p in overlay] == [8,9]
        assert all(not p['page_zones'] for p in overlay)
        assert overlay[0]['comment'] == 'Absent'
        assert copy_overlay_pages(db,db.get(Assessment,aid),copy,{},persist=False) == []


def test_reverse_duplex_keeps_front_back_together():
    from app.routers.printing import _reverse_pdf_pages
    from pypdf import PdfReader, PdfWriter
    writer=PdfWriter()
    for width in (101,102,103,104,105):writer.add_blank_page(width=width,height=200)
    buf=BytesIO();writer.write(buf)
    pages=PdfReader(BytesIO(_reverse_pdf_pages(buf.getvalue(),duplex=True))).pages
    assert [int(p.mediabox.width) for p in pages] == [105,105,103,104,101,102]
    single=PdfReader(BytesIO(_reverse_pdf_pages(buf.getvalue()))).pages
    assert [int(p.mediabox.width) for p in single] == [105,104,103,102,101]


def test_competency_printout_separates_common_and_complements(env):
    from pypdf import PdfReader
    client,factory,tokens,year=env
    with factory() as db:
        fw=db.query(CompetencyFramework).filter_by(path_id='ci-C',grade_level='tle').first()
        fid=fw.id
    path=f'/api/competencies/frameworks/{fid}/pdf'
    assert client.get(path,headers=tokens['corrector']).status_code == 422
    with factory() as db:
        db.add_all([Competency(framework_id=fid,code='A1',label='Calcul commun',section='common',order_index=2),
                    Competency(framework_id=fid,code='C1',label='Calcul complémentaire',section='complement',order_index=1)])
        db.commit()
    res=client.get(path,headers=tokens['corrector'])
    assert res.status_code == 200 and res.headers['content-type'] == 'application/pdf'
    text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(res.content)).pages)
    assert 'Terminale' in text and 'Programme ivoirien' in text
    assert text.index('Calcul commun') < text.index('Compléments') < text.index('Calcul complémentaire')


def test_collection_counts_age_boundary_and_recovery(env, monkeypatch):
    client, factory, tokens, year = env
    _, tid, cid, _, _ = create_teacher_class(env)
    current = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(operations, 'now', lambda: current)
    with factory() as db:
        cases = {}
        for title, delay in [('overdue', timedelta(days=8)), ('week', timedelta(days=7)),
                             ('just_over', timedelta(days=7, seconds=1)), ('recent', timedelta(days=6)),
                             ('recovered', timedelta(days=10)), ('scanned', timedelta(days=12)),
                             ('returned', timedelta(days=20)), ('not_delivered', None)]:
            a = Assessment(class_id=cid, teacher_profile_id=tid, title=title, status='printed',
                           subjects_delivered_at=current-delay if delay else None)
            if title == 'recovered': a.originals_received_at = current
            db.add(a); db.flush(); cases[title] = a.id
            if title in ('scanned', 'returned'):
                db.add(ScanBatch(assessment_id=a.id, status='overlay_ready' if title == 'returned' else 'pending',
                                 overlay_distributed=title == 'returned'))
        db.commit()
    response = client.get('/api/operations/dashboard', headers=tokens['corrector'])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['lots_at_schools'] == 4
    assert data['counts']['collect'] == 4
    assert data['overdue_collection_count'] == 2
    rows = {a['title']: a for a in data['assessments']}
    assert rows['overdue']['days_at_school'] == 8 and rows['overdue']['collection_overdue']
    assert rows['week']['days_at_school'] == 7 and not rows['week']['collection_overdue']
    assert rows['just_over']['days_at_school'] == 7 and rows['just_over']['collection_overdue']
    for title in ('recovered', 'scanned', 'returned', 'not_delivered'):
        assert not rows[title]['at_school'] and not rows[title]['collection_overdue']
        assert rows[title]['days_at_school'] is None
    # Répéter la confirmation de livraison ne remet pas le compteur à zéro.
    path = f'/api/operations/assessments/{cases["overdue"]}'
    assert client.patch(path, headers=tokens['corrector'], json={'subjects_delivered': True}).status_code == 200
    data = client.get('/api/operations/dashboard', headers=tokens['corrector']).json()
    assert data['overdue_collection_count'] == 2
    assert client.patch(path, headers=tokens['corrector'], json={'originals_received': True}).status_code == 200
    data = client.get('/api/operations/dashboard', headers=tokens['corrector']).json()
    assert data['lots_at_schools'] == 3 and data['overdue_collection_count'] == 1
    assert next(a for a in data['assessments'] if a['id'] == cases['overdue'])['stage'] == 'scan'


def test_collection_alerts_follow_school_and_year_filters(env, monkeypatch):
    client, factory, tokens, year = env
    sid, tid, cid, _, _ = create_teacher_class(env)
    current = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(operations, 'now', lambda: current)
    with factory() as db:
        other_school = School(name='Autre établissement', country_id='CI')
        past_year = SchoolYear(label='2025-2026', active=False)
        db.add_all([other_school, past_year]); db.flush()
        other_class = SchoolClass(name='3B', school_id=other_school.id, school_year_id=year, grade_level='3e')
        archived_class = SchoolClass(name='3A', school_id=sid, school_year_id=past_year.id, grade_level='3e', archived_at=current)
        db.add_all([other_class, archived_class]); db.flush()
        for class_id, days in [(cid, 3), (other_class.id, 9), (archived_class.id, 30)]:
            db.add(Assessment(class_id=class_id, title='Lot', status='printed', teacher_profile_id=tid,
                              subjects_delivered_at=current-timedelta(days=days)))
        db.commit(); past_year_id = past_year.id
    data = client.get('/api/operations/dashboard', headers=tokens['corrector']).json()
    assert data['lots_at_schools'] == 2 and data['overdue_collection_count'] == 1
    data = client.get(f'/api/operations/dashboard?school_id={sid}', headers=tokens['corrector']).json()
    assert data['lots_at_schools'] == 1 and data['overdue_collection_count'] == 0
    data = client.get(f'/api/operations/dashboard?school_id={sid}&year_id={past_year_id}', headers=tokens['corrector']).json()
    assert data['lots_at_schools'] == 1 and data['overdue_collection_count'] == 1


# ----------------------------------------------------- professeur autonome

def autonomous_teacher(env, name='Emile'):
    """Invité depuis Paramètres > Utilisateurs, puis inscrit avec son lien."""
    client, factory, tokens, year = env
    created = client.post('/api/admin/users/autonomous-teachers', headers=tokens['admin'], json={'name': name})
    assert created.status_code == 200, created.text
    token = created.json()['path'].split('invitation=')[1]
    signup = client.post('/api/auth/register', json={
        'token': token, 'email': name.lower()+'@test.fr', 'password': 'Long-password-123'})
    assert signup.status_code == 200, signup.text
    return {'Authorization': 'Bearer '+signup.json()['token']}


def new_class(client, headers, name='6e3', grade='6e', **extra):
    return client.post('/api/classes', headers=headers, json={
        'name': name, 'grade_level': grade, 'students_text': 'Alice\nBenoît', **extra})


def choose(client, headers, program='fr', subject='math'):
    return client.put('/api/auth/me/teaching', headers=headers, json={'program_id': program, 'subject_id': subject})


def test_autonomous_teacher_chooses_program_and_subject_and_needs_no_school(env):
    client, factory, tokens, year = env
    h = autonomous_teacher(env)
    me = client.get('/api/auth/me', headers=h).json()
    assert me['service_mode'] == 'autonomous' and me['program_id'] is None
    assert me['teaching']['cycles'] == []   # aucune classe : aucun cycle
    refused = new_class(client, h)
    assert refused.status_code == 422 and 'Paramètres' in refused.json()['detail']

    chosen = choose(client, h)
    assert chosen.status_code == 200, chosen.text
    assert chosen.json() == {'cycles': [], 'subjects': [{'id': 'math', 'name': 'Mathématiques'}], 'programs': ['fr']}

    # Les cycles viennent des classes créées, dans l'ordre scolaire.
    for name, grade in (('3e6', '3e'), ('6e3', '6e'), ('6e4', '6e')):
        made = new_class(client, h, name, grade, program_id='ci', subject_id='physics')
        assert made.status_code == 200, made.text
    me = client.get('/api/auth/me', headers=h).json()
    assert me['program_id'] == 'fr' and me['subject_id'] == 'math'
    assert me['teaching']['cycles'] == ['6e', '3e'] and me['teaching']['programs'] == ['fr']
    rows = {c['name']: c for c in client.get('/api/classes', headers=h).json()}
    assert set(rows) == {'3e6', '6e3', '6e4'}
    assert all(c['school_id'] is None and c['program_id'] == 'fr' for c in rows.values())

    # Il crée ses sujets et voit ses seules classes, sans aucun établissement.
    cid = rows['6e3']['id']
    assert new_class(client, h, '6e3', '6e').status_code == 409
    assert client.patch(f'/api/classes/{cid}', headers=h, json={'name': '6e4'}).status_code == 409
    assert client.patch(f'/api/classes/{cid}', headers=h, json={'name': '6e5'}).status_code == 200
    made = client.post('/api/assessments', headers=h, json={'class_id': cid, 'title': 'Angles'})
    assert made.status_code == 200, made.text
    assert client.get('/api/operations/schools', headers=h).json() == []
    assert client.get('/api/operations/teachers', headers=h).status_code == 403


def test_two_autonomous_teachers_can_name_a_class_alike(env):
    client, factory, tokens, year = env
    h1, h2 = autonomous_teacher(env), autonomous_teacher(env, 'Lina')
    for h in (h1, h2):
        assert choose(client, h).status_code == 200
        made = new_class(client, h)
        assert made.status_code == 200, made.text
    assert len(client.get('/api/classes', headers=h1).json()) == 1


def test_establishments_only_supervise_accompanied_teachers(env):
    client, factory, tokens, year = env
    sid, tid, cid, _, _ = create_teacher_class(env)
    h = autonomous_teacher(env)
    assert choose(client, h).status_code == 200
    own = new_class(client, h).json()['id']
    assert client.post('/api/assessments', headers=h, json={'class_id': own, 'title': 'Autonome'}).status_code == 200
    assert client.post('/api/assessments', headers=tokens['admin'], json={'class_id': cid, 'title': 'Accompagné'}).status_code == 200
    with factory() as db:
        autonomous = db.query(TeacherProfile).filter_by(service_mode='autonomous').one().id

    for h_op in (tokens['admin'], tokens['corrector']):
        assert [t['id'] for t in client.get('/api/operations/teachers', headers=h_op).json()] == [tid]
        board = client.get('/api/operations/dashboard', headers=h_op).json()
        assert [a['title'] for a in board['assessments']] == ['Accompagné']
    body = {'name': 'X', 'school_ids': [sid]}
    assert client.put(f'/api/operations/teachers/{autonomous}', headers=tokens['admin'], json=body).status_code == 404
    assert client.post(f'/api/operations/teachers/{autonomous}/invitation', headers=tokens['admin']).status_code == 404
    assert client.put(f'/api/operations/classes/{own}/teaching', headers=tokens['admin'], json={
        'subject_id': 'math', 'teacher_id': autonomous}).status_code == 422
    # Un mode envoyé par un ancien client ne fait plus entrer d'autonome ici.
    made = client.post('/api/operations/teachers', headers=tokens['admin'], json={
        'name': 'Sam', 'school_ids': [sid], 'service_mode': 'autonomous'})
    with factory() as db:
        assert db.get(TeacherProfile, made.json()['id']).service_mode == 'accompanied'


def test_accompanied_teacher_keeps_school_and_cannot_choose_teaching(env):
    client, factory, tokens, year = env
    sid, tid, cid, th, _ = create_teacher_class(env)
    assert choose(client, th).status_code == 403
    assert new_class(client, th, '4B', '4e').status_code == 422
    made = new_class(client, th, '4B', '4e', school_id=sid, program_id='fr')
    assert made.status_code == 200, made.text
    # Une classe sans établissement reste refusée à un administrateur sans fiche autonome.
    assert new_class(client, tokens['admin']).status_code == 422


def test_changing_program_follows_classes_until_they_have_subjects(env):
    client, factory, tokens, year = env
    h = autonomous_teacher(env)
    assert choose(client, h).status_code == 200
    cid = new_class(client, h).json()['id']
    assert choose(client, h, 'ci').status_code == 200
    assert client.get('/api/classes', headers=h).json()[0]['program_id'] == 'ci'
    assert choose(client, h, 'fr').status_code == 200
    assert client.post('/api/assessments', headers=h, json={'class_id': cid, 'title': 'Angles'}).status_code == 200
    refused = choose(client, h, 'ci')
    assert refused.status_code == 409 and 'sujets' in refused.json()['detail']
    assert client.get('/api/auth/me', headers=h).json()['program_id'] == 'fr'
    # Un lycée ivoirien exige une série : refus plutôt qu'une classe invalide.
    h2 = autonomous_teacher(env, 'Lina')
    assert choose(client, h2).status_code == 200
    assert new_class(client, h2, 'T1', 'tle').status_code == 200
    assert choose(client, h2, 'ci').status_code == 409


def test_changing_subject_keeps_access_to_existing_subjects(env):
    client, factory, tokens, year = env
    assert client.post('/api/operations/subjects', headers=tokens['admin'], json={'id': 'physics', 'name': 'Physique'}).status_code == 200
    h = autonomous_teacher(env)
    assert choose(client, h).status_code == 200
    with_subject = new_class(client, h, '6e3').json()['id']
    empty = new_class(client, h, '6e4').json()['id']
    assert client.post('/api/assessments', headers=h, json={'class_id': with_subject, 'title': 'Angles'}).status_code == 200
    assert choose(client, h, 'fr', 'physics').status_code == 200
    with factory() as db:
        taught = lambda class_id: sorted(t.subject_id for t in db.query(ClassTeaching).filter_by(class_id=class_id))
        assert taught(with_subject) == ['math', 'physics'] and taught(empty) == ['physics']
    assert [a['title'] for a in client.get('/api/assessments', headers=h).json()] == ['Angles']
    assert client.get('/api/auth/me', headers=h).json()['teaching']['subjects'] == [{'id': 'physics', 'name': 'Physique'}]


def test_pending_autonomous_invitations_are_listed_and_renewable(env):
    client, factory, tokens, year = env
    created = client.post('/api/admin/users/autonomous-teachers', headers=tokens['admin'], json={'name': 'Nora'})
    pending = client.get('/api/admin/users/autonomous-teachers', headers=tokens['admin']).json()
    assert [t['name'] for t in pending] == ['Nora']
    renewed = client.post(f"/api/admin/users/autonomous-teachers/{pending[0]['id']}/invitation", headers=tokens['admin'])
    assert renewed.status_code == 200, renewed.text
    old = created.json()['path'].split('invitation=')[1]
    assert client.post('/api/auth/invitation/check', json={'token': old}).status_code == 410
    new = renewed.json()['path'].split('invitation=')[1]
    assert client.post('/api/auth/invitation/check', json={'token': new}).json()['service_mode'] == 'autonomous'
    assert client.post('/api/admin/users/autonomous-teachers', headers=tokens['corrector'], json={'name': 'X'}).status_code == 403
