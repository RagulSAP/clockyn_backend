from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class EmployeePersonalDetails(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    branch_name: Optional[str] = None
    dept_name: Optional[str] = None
    role_name: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    address: Optional[str] = None
    emergency_contact_no: Optional[str] = None
    acc_no: Optional[str] = None
    ifsc_code: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    shift_id: Optional[int] = None
    father_name: Optional[str] = None
    marital_status: Optional[str] = None
    aadhaar_no: Optional[str] = None
    pan_no: Optional[str] = None
    uan_no: Optional[str] = None
    pf_no: Optional[str] = None
    esi_no: Optional[str] = None
    doj: Optional[str] = None
    profile_photo_url: Optional[str] = None

class UpdateEmployeePersonalDetails(BaseModel):
    name: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    address: Optional[str] = None
    emergency_contact_no: Optional[str] = None
    acc_no: Optional[str] = None
    ifsc_code: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    father_name: Optional[str] = None
    marital_status: Optional[str] = None
    aadhaar_no: Optional[str] = None
    pan_no: Optional[str] = None
    uan_no: Optional[str] = None
    pf_no: Optional[str] = None
    esi_no: Optional[str] = None
    doj: Optional[str] = None

async def get_employee_personal_details(user_id: str):
    """Get personal details for employee (role_id 2 or 3) from users and users_additional_info tables"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email,
                   b.branch_name, d.dept_name, r.role_name,
                   TO_CHAR(ua.dob, 'YYYY-MM-DD') as dob, 
                   ua.gender, ua.blood_group, ua.address, 
                   ua.emergency_contact_no, ua.acc_no, ua.ifsc_code, 
                   ua.bank_name, ua.bank_branch, ua.shift_id,
                   ua.father_name, ua.marital_status, ua.aadhaar_no,
                   ua.pan_no, ua.uan_no, ua.pf_no, ua.esi_no,
                   TO_CHAR(ua.doj, 'YYYY-MM-DD') as doj
            FROM users u
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN role_info r ON u.role_id = r.role_id
            LEFT JOIN users_additional_info ua ON u.user_id = ua.user_id
            WHERE u.user_id = %s AND u.role_id IN (2, 3) AND u.status = 1
        """
        
        cursor.execute(query, (user_id,))
        employee = cursor.fetchone()
        
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found or not authorized")
        
        from ml.s3 import get_employee_profile_url
        employee_dict = dict(employee)
        employee_dict['profile_photo_url'] = get_employee_profile_url(user_id)
        return EmployeePersonalDetails(**employee_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get employee details: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def update_employee_personal_details(user_id: str, data: UpdateEmployeePersonalDetails):
    """Update personal details for employee in users_additional_info table"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if user exists and is employee
        cursor.execute("SELECT user_id FROM users WHERE user_id = %s AND role_id IN (2, 3) AND status = 1", (user_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Update users table if name, mobile or email provided
        if data.name or data.mobile_number or data.email:
            update_fields = []
            values = []
            if data.name:
                update_fields.append("name = %s")
                values.append(data.name)
            if data.mobile_number:
                update_fields.append("mobile_number = %s")
                values.append(data.mobile_number)
            if data.email:
                update_fields.append("email = %s")
                values.append(data.email)
            if update_fields:
                values.append(user_id)
                query = f"UPDATE users SET {', '.join(update_fields)} WHERE user_id = %s"
                cursor.execute(query, values)
        
        # Check if record exists in users_additional_info
        cursor.execute("SELECT user_id FROM users_additional_info WHERE user_id = %s", (user_id,))
        exists = cursor.fetchone()
        
        # Parse dates if provided
        dob_value = None
        if data.dob:
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d%m%Y', '%m%d%Y'):
                try:
                    dob_value = datetime.strptime(data.dob, fmt).date()
                    break
                except:
                    pass
        
        doj_value = None
        if data.doj:
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d%m%Y', '%m%d%Y'):
                try:
                    doj_value = datetime.strptime(data.doj, fmt).date()
                    break
                except:
                    pass
        print("DOB RAW:", data.dob, "PARSED:", dob_value)
        print("DOJ RAW:", data.doj, "PARSED:", doj_value)
        
        additional_fields = {
            'dob': dob_value,
            'gender': data.gender,
            'blood_group': data.blood_group,
            'address': data.address,
            'emergency_contact_no': data.emergency_contact_no,
            'acc_no': data.acc_no,
            'ifsc_code': data.ifsc_code,
            'bank_name': data.bank_name,
            'bank_branch': data.bank_branch,
            'father_name': data.father_name,
            'marital_status': data.marital_status,
            'aadhaar_no': data.aadhaar_no,
            'pan_no': data.pan_no,
            'uan_no': data.uan_no,
            'pf_no': data.pf_no,
            'esi_no': data.esi_no,
            'doj': doj_value,
        }
        non_null_fields = {k: v for k, v in additional_fields.items() if v is not None and v != ''}
        print("DATA RECEIVED:", data)
        print("NON NULL FIELDS:", non_null_fields)
        print("EXISTS:", exists)

        if exists:
            if non_null_fields:
                set_clause = ', '.join(f"{k} = %s" for k in non_null_fields)
                query = f"UPDATE users_additional_info SET {set_clause} WHERE user_id = %s"
                cursor.execute(query, (*non_null_fields.values(), user_id))
        else:
            insert_fields = {'user_id': user_id, **non_null_fields}
            cols = ', '.join(insert_fields.keys())
            placeholders = ', '.join(['%s'] * len(insert_fields))
            query = f"INSERT INTO users_additional_info ({cols}) VALUES ({placeholders})"
            cursor.execute(query, tuple(insert_fields.values()))
        
        conn.commit()
        return {"success": True, "message": "Personal details updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to update personal details: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)
