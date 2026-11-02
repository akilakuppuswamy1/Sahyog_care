"""Front-desk operations. Caller holds the Core app lock; SQL writes are transactional."""
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import HTTPException
from services.common.timefmt import ist
from services.common.tracing import trace_id_for


def rows(conn, sql, args=()):
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, args).fetchall()


def audit(deps, staff, action, resource, rid, patient=None, trace_id=None):
    trace_id = trace_id_for(getattr(deps, "tracer", None), trace_id) or uuid4().hex
    detail = {"trace_id": trace_id}
    if hasattr(deps.store, 'conn'):
        deps.store.conn.execute("""INSERT INTO audit_event(actor_id,actor_role,action,resource_type,resource_id,
            patient_id,purpose,trace_id,detail) VALUES (%s,'front_desk',%s,%s,%s,%s,'frontdesk',%s,%s)""",
                                (staff,action,resource,str(rid),patient,trace_id,json.dumps(detail)))
    else:
        deps.store.events.append(dict(actor_id=staff,actor_role='front_desk',action=action,resource_type=resource,
                                     resource_id=str(rid),patient_id=patient,purpose='frontdesk',
                                     trace_id=trace_id,detail=detail))


def escalations(deps, status):
    if hasattr(deps.store, 'conn'):
        return rows(deps.store.conn,"""SELECT a.event_id AS id,a.patient_id,a.purpose,a.detail,a.at,
            coalesce(h.status,'open') AS status,h.handled_by FROM audit_event a
            LEFT JOIN frontdesk_handling h USING(event_id) WHERE a.action='escalate' AND a.resource_type='w1_triage'
            AND coalesce(h.status,'open')=%s ORDER BY a.at""",(status,))
    return [dict(e,id=i,status='handled' if i in getattr(deps.store,'handled',{}) else 'open')
            for i,e in enumerate(deps.store.events) if e.get('action')=='escalate'
            and ('handled' if i in getattr(deps.store,'handled',{}) else 'open')==status]


def handled(deps, eid, staff):
    if hasattr(deps.store,'conn'):
        c=deps.store.conn
        with c.transaction():
            found=c.execute("SELECT event_id FROM audit_event WHERE event_id=%s AND action='escalate' AND resource_type='w1_triage'",(eid,)).fetchone()
            if not found: raise HTTPException(404,'Escalation not found')
            c.execute("INSERT INTO frontdesk_handling(event_id,handled_by) VALUES (%s,%s) ON CONFLICT DO NOTHING",(eid,staff))
            audit(deps,staff,'write','escalation',eid)
    else:
        if not any(e['id']==eid for e in escalations(deps,'open')+escalations(deps,'handled')): raise HTTPException(404,'Escalation not found')
        if not hasattr(deps.store,'handled'): deps.store.handled={}
        deps.store.handled.setdefault(eid,staff)
        audit(deps,staff,'write','escalation',eid)
    return dict(id=eid,status='handled')


def bookings(deps, booked_by):
    if hasattr(deps.store,'conn'):
        result=rows(deps.store.conn,"""SELECT a.*,s.starts_at,s.doctor_id,s.clinic_id,d.name AS doctor,c.name AS clinic
          FROM appointment a JOIN slot s USING(slot_id) JOIN doctor d USING(doctor_id)
          JOIN clinic c ON c.clinic_id=s.clinic_id WHERE a.booked_by=%s ORDER BY s.starts_at""",(booked_by,))
        for r in result: r['starts_at_display']=ist(r['starts_at'])
        return result
    return [dict(a,**next(d for d in deps.store.doctors if d['slot_id']==a['slot_id']),starts_at_display=ist(next(d['starts_at'] for d in deps.store.doctors if d['slot_id']==a['slot_id'])))
            for a in getattr(deps.store,'appointments',{}).values() if a['booked_by']==booked_by]


def override(deps,staff,aid,slot,key):
    fingerprint=json.dumps([aid,slot])
    if hasattr(deps.store,'conn'):
        c=deps.store.conn
        try: UUID(aid)
        except ValueError: raise HTTPException(404,'Booking not found')
        with c.transaction():
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(staff+':'+key,))
            replay=c.execute('SELECT fingerprint,result FROM frontdesk_replay WHERE staff_id=%s AND idempotency_key=%s',(staff,key)).fetchone()
            if replay:
                if replay[0]!=fingerprint: raise HTTPException(409,'Idempotency key belongs to another action')
                audit(deps,staff,'read','appointment',aid)
                return replay[1]
            a=c.execute('SELECT patient_id,status FROM appointment WHERE appt_id=%s FOR UPDATE',(aid,)).fetchone()
            if not a: raise HTTPException(404,'Booking not found')
            if a[1]!='booked': raise HTTPException(409,'Booking is no longer active')
            c.execute("SELECT release_appointment(%s,'overridden')",(aid,))
            new=None
            if slot:
                import psycopg
                try: new=str(c.execute("SELECT book_slot(%s,%s,%s,'front_desk')",(a[0],slot,'frontdesk:'+staff+':'+key)).fetchone()[0])
                except psycopg.errors.RaiseException: raise HTTPException(409,'Slot unavailable')
            result=dict(appt_id=aid,status='overridden',new_appt_id=new)
            audit(deps,staff,'override','appointment',aid,a[0])
            if new: audit(deps,staff,'write','appointment',new,a[0])
            c.execute('INSERT INTO frontdesk_replay VALUES (%s,%s,%s,%s)',(staff,key,fingerprint,json.dumps(result)))
            return result
    store=deps.store
    if not hasattr(store,'front_replays'): store.front_replays={}
    replay=store.front_replays.get((staff,key))
    if replay:
        if replay[0]!=fingerprint: raise HTTPException(409,'Idempotency key belongs to another action')
        audit(deps,staff,'read','appointment',aid)
        return replay[1]
    a=getattr(store,'appointments',{}).get(aid)
    if not a: raise HTTPException(404,'Booking not found')
    if a['status']!='booked': raise HTTPException(409,'Booking is no longer active')
    if slot and slot!=a['slot_id'] and (slot in store.booked or slot not in {d['slot_id'] for d in store.doctors}): raise HTTPException(409,'Slot unavailable')
    store.booked.pop(a['slot_id'],None); a['status']='overridden'
    new=store.book(a['patient_id'],slot,'frontdesk:'+staff+':'+key) if slot else None
    if new: store.appointments[new]['booked_by']='front_desk'
    result=dict(appt_id=aid,status='overridden',new_appt_id=new)
    store.front_replays[staff,key]=(fingerprint,result)
    audit(deps,staff,'override','appointment',aid,a['patient_id'])
    if new: audit(deps,staff,'write','appointment',new,a['patient_id'])
    return result
