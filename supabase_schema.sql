-- God Dominion School portal: Supabase / Postgres schema
-- Paste into Supabase → SQL Editor → Run (optional; the app also creates these automatically)

CREATE TABLE IF NOT EXISTS academic_session (
	id SERIAL NOT NULL, 
	name VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS announcement (
	id SERIAL NOT NULL, 
	title VARCHAR(128) NOT NULL, 
	body TEXT NOT NULL, 
	audience VARCHAR(16), 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS contact_message (
	id SERIAL NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	email VARCHAR(128), 
	phone VARCHAR(32), 
	subject VARCHAR(64), 
	message TEXT NOT NULL, 
	read BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS email_log (
	id SERIAL NOT NULL, 
	"to" VARCHAR(200) NOT NULL, 
	subject VARCHAR(200) NOT NULL, 
	body TEXT NOT NULL, 
	kind VARCHAR(32), 
	status VARCHAR(16), 
	error VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS gallery_item (
	id SERIAL NOT NULL, 
	image_url VARCHAR(500) NOT NULL, 
	caption VARCHAR(160), 
	category VARCHAR(32), 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS post (
	id SERIAL NOT NULL, 
	kind VARCHAR(8), 
	title VARCHAR(160) NOT NULL, 
	slug VARCHAR(180) NOT NULL, 
	summary VARCHAR(300), 
	body TEXT, 
	image_url VARCHAR(500), 
	event_date DATE, 
	event_time VARCHAR(32), 
	venue VARCHAR(128), 
	published BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (slug)
);

CREATE TABLE IF NOT EXISTS setting (
	key VARCHAR(64) NOT NULL, 
	value TEXT, 
	PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS subject (
	id SERIAL NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	code VARCHAR(16), 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS testimonial (
	id SERIAL NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	role VARCHAR(80), 
	text TEXT NOT NULL, 
	show BOOLEAN, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS "user" (
	id SERIAL NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	full_name VARCHAR(128) NOT NULL, 
	email VARCHAR(128), 
	phone VARCHAR(32), 
	active BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

CREATE TABLE IF NOT EXISTS fee_item (
	id SERIAL NOT NULL, 
	session_id INTEGER NOT NULL, 
	term VARCHAR(16) NOT NULL, 
	level VARCHAR(16), 
	name VARCHAR(64) NOT NULL, 
	amount FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES academic_session (id)
);

CREATE TABLE IF NOT EXISTS school_class (
	id SERIAL NOT NULL, 
	name VARCHAR(32) NOT NULL, 
	level VARCHAR(16) NOT NULL, 
	form_teacher_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	FOREIGN KEY(form_teacher_id) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS class_subject (
	id SERIAL NOT NULL, 
	class_id INTEGER NOT NULL, 
	subject_id INTEGER NOT NULL, 
	teacher_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (class_id, subject_id), 
	FOREIGN KEY(class_id) REFERENCES school_class (id), 
	FOREIGN KEY(subject_id) REFERENCES subject (id), 
	FOREIGN KEY(teacher_id) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS publication (
	id SERIAL NOT NULL, 
	class_id INTEGER NOT NULL, 
	session_id INTEGER NOT NULL, 
	term VARCHAR(16) NOT NULL, 
	published_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (class_id, session_id, term), 
	FOREIGN KEY(class_id) REFERENCES school_class (id), 
	FOREIGN KEY(session_id) REFERENCES academic_session (id)
);

CREATE TABLE IF NOT EXISTS student (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	reg_no VARCHAR(32) NOT NULL, 
	class_id INTEGER, 
	gender VARCHAR(8), 
	dob VARCHAR(16), 
	guardian_name VARCHAR(128), 
	guardian_phone VARCHAR(32), 
	address VARCHAR(256), 
	status VARCHAR(16), 
	guardian_email VARCHAR(128), 
	photo_url VARCHAR(500), 
	blood_group VARCHAR(8), 
	medical_note VARCHAR(300), 
	parent_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES "user" (id), 
	UNIQUE (reg_no), 
	FOREIGN KEY(class_id) REFERENCES school_class (id), 
	FOREIGN KEY(parent_id) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS timetable_slot (
	id SERIAL NOT NULL, 
	class_id INTEGER NOT NULL, 
	day INTEGER NOT NULL, 
	period INTEGER NOT NULL, 
	subject_id INTEGER, 
	label VARCHAR(32), 
	PRIMARY KEY (id), 
	UNIQUE (class_id, day, period), 
	FOREIGN KEY(class_id) REFERENCES school_class (id), 
	FOREIGN KEY(subject_id) REFERENCES subject (id)
);

CREATE TABLE IF NOT EXISTS application (
	id SERIAL NOT NULL, 
	app_no VARCHAR(32) NOT NULL, 
	full_name VARCHAR(128) NOT NULL, 
	dob VARCHAR(16), 
	gender VARCHAR(8), 
	class_sought VARCHAR(16), 
	previous_school VARCHAR(160), 
	parent_name VARCHAR(128) NOT NULL, 
	relationship VARCHAR(32), 
	phone VARCHAR(32) NOT NULL, 
	email VARCHAR(128), 
	address VARCHAR(256), 
	heard_from VARCHAR(64), 
	notes TEXT, 
	photo_url VARCHAR(500), 
	status VARCHAR(16), 
	exam_date VARCHAR(64), 
	admin_note TEXT, 
	student_id INTEGER, 
	token VARCHAR(40), 
	form_paid BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (app_no), 
	FOREIGN KEY(student_id) REFERENCES student (id), 
	UNIQUE (token)
);

CREATE TABLE IF NOT EXISTS attendance (
	id SERIAL NOT NULL, 
	student_id INTEGER NOT NULL, 
	class_id INTEGER NOT NULL, 
	session_id INTEGER NOT NULL, 
	term VARCHAR(16) NOT NULL, 
	day DATE NOT NULL, 
	status VARCHAR(1) NOT NULL, 
	marked_by INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (student_id, day), 
	FOREIGN KEY(student_id) REFERENCES student (id), 
	FOREIGN KEY(class_id) REFERENCES school_class (id), 
	FOREIGN KEY(session_id) REFERENCES academic_session (id), 
	FOREIGN KEY(marked_by) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS homework (
	id SERIAL NOT NULL, 
	class_subject_id INTEGER NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	details TEXT, 
	file_url VARCHAR(500), 
	due_date DATE, 
	teacher_id INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(class_subject_id) REFERENCES class_subject (id), 
	FOREIGN KEY(teacher_id) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS remark (
	id SERIAL NOT NULL, 
	student_id INTEGER NOT NULL, 
	session_id INTEGER NOT NULL, 
	term VARCHAR(16) NOT NULL, 
	teacher_comment TEXT, 
	principal_comment TEXT, 
	days_present INTEGER, 
	days_open INTEGER, 
	punctuality VARCHAR(2), 
	neatness VARCHAR(2), 
	conduct VARCHAR(2), 
	PRIMARY KEY (id), 
	UNIQUE (student_id, session_id, term), 
	FOREIGN KEY(student_id) REFERENCES student (id), 
	FOREIGN KEY(session_id) REFERENCES academic_session (id)
);

CREATE TABLE IF NOT EXISTS score (
	id SERIAL NOT NULL, 
	student_id INTEGER NOT NULL, 
	subject_id INTEGER NOT NULL, 
	class_id INTEGER NOT NULL, 
	session_id INTEGER NOT NULL, 
	term VARCHAR(16) NOT NULL, 
	ca1 FLOAT, 
	ca2 FLOAT, 
	exam FLOAT, 
	updated_by INTEGER, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (student_id, subject_id, session_id, term), 
	FOREIGN KEY(student_id) REFERENCES student (id), 
	FOREIGN KEY(subject_id) REFERENCES subject (id), 
	FOREIGN KEY(class_id) REFERENCES school_class (id), 
	FOREIGN KEY(session_id) REFERENCES academic_session (id), 
	FOREIGN KEY(updated_by) REFERENCES "user" (id)
);

CREATE TABLE IF NOT EXISTS scratch_card (
	id SERIAL NOT NULL, 
	serial VARCHAR(16) NOT NULL, 
	pin VARCHAR(16) NOT NULL, 
	max_uses INTEGER, 
	used_count INTEGER, 
	student_id INTEGER, 
	session_id INTEGER, 
	term VARCHAR(16), 
	batch VARCHAR(32), 
	source VARCHAR(16), 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (serial), 
	UNIQUE (pin), 
	FOREIGN KEY(student_id) REFERENCES student (id), 
	FOREIGN KEY(session_id) REFERENCES academic_session (id)
);

CREATE TABLE IF NOT EXISTS payment (
	id SERIAL NOT NULL, 
	student_id INTEGER, 
	application_id INTEGER, 
	session_id INTEGER, 
	term VARCHAR(16), 
	purpose VARCHAR(16), 
	amount FLOAT NOT NULL, 
	reference VARCHAR(64) NOT NULL, 
	status VARCHAR(16), 
	channel VARCHAR(16), 
	payer_email VARCHAR(128), 
	paid_by_user_id INTEGER, 
	gateway_id VARCHAR(64), 
	note VARCHAR(256), 
	card_id INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	paid_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student (id), 
	FOREIGN KEY(application_id) REFERENCES application (id), 
	FOREIGN KEY(session_id) REFERENCES academic_session (id), 
	UNIQUE (reference), 
	FOREIGN KEY(paid_by_user_id) REFERENCES "user" (id), 
	FOREIGN KEY(card_id) REFERENCES scratch_card (id)
);
