# ISS jobs server
# June 2022
# Max Shinn <m.shinn@ucl.ac.uk>
#
# This requires the flask library to be installed.  Obviously it also requires
# iss to be installed.  To set up this server, set the all caps directories
# their relevant values.  They only need to be writable if the ini file has
# them for its output location.  You also need to set up an "output directory"
# for all of the jobs and their log files to be stored.  Likewise, set up a
# "password file", a single plain text file containing only a password.  This
# must be outside of the git repo.
import flask
import re
import os
import datetime
import subprocess
import glob

OUTPUT_DIR = "/home/max/issjobs/issjobs_files" # No trailing slash
PASSWORD_FILE = "/home/max/issjobs/password.txt" # Path to a text file which should contain one line, the password
PASSWORD_ACCESS_LOG = "/home/max/issjobs/password_log.txt" # Should initially be an empty file in a writable location, not in the repo
PYTHON_PATH = "/usr/bin/python3"
PATH_TO_ZINU = "/home/max/servers/zinu" # No trailing slash
PATH_TO_ZSERVER = "/home/max/servers/zserver" # No trailing slash

# We need to have a password for security reasons.  This entire script is not
# at all secure, since it runs arbitrary code on a remote computer via HTTP, so
# rather than trying to sanitise the .ini file, we just use a password as a
# cheap trick to make sure only trusted users can upload.
def check_password(p):
    current_dir = os.path.dirname(os.path.realpath(__file__))
    if current_dir in PASSWORD_FILE:
        exit("Error, password file must not be contained in the source directory.  This is to avoid accidentally committing it to the git repo.")
    if "/" not in PASSWORD_FILE.strip(".").strip("/"):
        exit("Error, password file must not be a short relative path.  This is to avoid accidentally committing it to the git repo.")
    with open(PASSWORD_FILE, "r") as f:
        password = f.read().strip()
    return p == password

# This will not only check whether the password is empty, but will also ensure
# that it crashes immediately after it is run instead of while a user is
# submitting.
assert not check_password(""), "Password must not be empty"

# We really have no security on this so I improvised a security method.  If the
# password is incorrect, log the incorrect password into the access log, along
# with the date and the ip.  Once we reach 100 incorrect attempts, shut down
# the whole system.  Then someone can come look at the log and see if it looks
# sketchy.  If not, just delete the log.  If so, then someone is trying to
# access the system and this could be VERY bad.
def log_incorrect_password(p):
    logline = f"date={datetime.datetime.today()}, ip={flask.request.remote_addr}, password={p}\n"
    with open(PASSWORD_ACCESS_LOG, "a") as f:
        f.write(logline)

# Check to make sure that no more than 100 total incorrect passwords have
# occurred.  This will probably fill up at some point from healthy usage and
# need to be emptied by an admin.  But when emptying, the admin should always
# look to make sure they're not all from the same IP and that the passwords are
# reasonable accidents, not a brute force attack.
def confirm_password_log_not_full():
    with open(PASSWORD_ACCESS_LOG, "r") as f:
        nlines = len(f.readlines())
    return nlines < 100


# Choose a valid name for the job based on the one the user has optionally
# specified.
def jobname(chosen=""):
    sanitised = re.sub(r'[^A-Za-z0-9_]', '', chosen)
    if sanitised == "":
        sanitised = "job"
    sanitised = datetime.datetime.today().strftime('%Y-%m-%d_%H-%M-%S') + "_" + sanitised
    suffix = 0
    jname = sanitised
    while os.path.isdir(OUTPUT_DIR + "/"+jname):
        jname = sanitised + str(suffix)
        suffix += 1
    #os.mkdir(OUTPUT_DIR + jname)
    return jname

app = flask.Flask(__name__)

# Serve the CSS
@app.route("/beauter.min.css")
def beauter():
    with open("beauter.min.css") as f:
        return flask.Response(f.read(), mimetype='text/css')

# The home page where users may submit jobs
@app.route("/")
def home():
    with open("_index_template.html") as f:
        template = f.read()
    if "m" in flask.request.args:
        m_safe = re.sub(r'[^A-Za-z0-9 ]', '', flask.request.args['m'])
        m = f"<div class=\"alert _warning _shadow\">{m_safe}</div><br />"
        template = template.replace("%MESSAGE%", m)
    else:
        template = template.replace("%MESSAGE%", "")
    return template

# POST requests to submit new jobs are sent here.
@app.route("/submit", methods=["POST"])
def submit():
    resp = flask.request.form
    if not confirm_password_log_not_full():
        return "Urgent system error, please contact an admin immediately, who will be able to quickly figure out what to do once they see the server's source code."
    if not check_password(resp['pass']):
        log_incorrect_password(resp['pass'])
        return flask.redirect("/?m=Password incorrect")
    name = jobname(resp['jobname'])
    f = flask.request.files['inifile']
    if f.filename == "":
        return flask.redirect("/?m=No file uploaded")
    os.mkdir(f"{OUTPUT_DIR}/{name}/")
    f.save(f"{OUTPUT_DIR}/{name}/config.original.ini")
    fix_ini(name)
    run(name, resp['username'])
    return flask.redirect(f"/view?job={name}")

def fix_ini(name):
    """Modify the .ini config file.

    The config file will often use the Windows name for the server instead of
    the Linux name.
    """
    with open(f"{OUTPUT_DIR}/{name}/config.original.ini", "r") as f:
        contents = f.read()
    # Switch the filenames
    contents = re.sub(r"\\zinu(\.cortexlab\.net)?\\", PATH_TO_ZINU, contents, flags=re.IGNORECASE)
    contents = re.sub(r"\\zserver(\.cortexlab\.net)?\\", PATH_TO_ZSERVER, contents, flags=re.IGNORECASE)
    contents = contents.replace(r"\\", r"/")
    contents = ";;; WARNING - This file is autogenerated from the real config file config.original.ini.  Paths and escape sequences have been modified.\n\n\n" + contents
    with open(f"{OUTPUT_DIR}/{name}/config.ini", "w") as f:
        f.write(contents)

# Run the ISS software
def run(name, person):
    with open(f"{OUTPUT_DIR}/{name}/output.log", "w") as f:
        f.write(f"Started output for job '{name}', user '{person}' at {datetime.datetime.today().strftime('%Y-%m-%d %H:%M:%S')}\n")
    subprocess.Popen(f"{PYTHON_PATH} -m iss {OUTPUT_DIR}/{name}/config.ini >> {OUTPUT_DIR}/{name}/output.log 2>&1 && touch {OUTPUT_DIR}/{name}/success || touch {OUTPUT_DIR}/{name}/complete", shell=True)

# View either a list of all previous jobs or of a specific job (given by the
# "job" GET argument)
@app.route("/view")
def view():
    with open("_list_template.html") as f:
        template = f.read()
    # Handle the case of viewing a single record
    if "job" in flask.request.args:
        job_safe = re.sub(r'[^A-Za-z0-9_-]', '', flask.request.args['job'])
        if job_safe != flask.request.args['job']:
            return flask.redirect("/?m=Invalid file")
        with open(f"{OUTPUT_DIR}/{job_safe}/output.log") as f:
            contents = f.read()
        output = f"<h2>{job_safe}</h2>\n\n<pre style=\"background-color: #E5E5E5; padding: 15px\">\n" + str(flask.escape(contents)) + "</pre>\n"
        if os.path.isfile(f"{OUTPUT_DIR}/{job_safe}/success"):
            output += f"<div class=\"alert _success _shadow\">Job completed successfully</div><br />"
        elif os.path.isfile(f"{OUTPUT_DIR}/{job_safe}/complete"):
            output += f"<div class=\"alert _shadow\">Job completed with errors</div><br />"
        else:
            output += "<button onclick=\"window.location.reload();\">Refresh</button>"
    # Handle the case of viewing all records in a list
    else:
        log_files = sorted(glob.glob(f"{OUTPUT_DIR}/*/output.log"))[::-1]
        output = "<h2>All jobs</h2>\n"
        for f in log_files:
            name = f.split("/")[-2]
            output += f"<li><a href=\"/view?job={name}\">{name}</a></li>\n"
        output = "<ul>\n" + output + "</ul>\n"
    template = template.replace("%MESSAGE%", output)
    return template

