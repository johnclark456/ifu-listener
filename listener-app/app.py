from flask import Flask, render_template, redirect, url_for, flash
from flask_wtf import FlaskForm
from wtforms import FloatField, IntegerField
from wtforms.validators import DataRequired, Optional, length
from flask_wtf.file import FileField
from werkzeug.utils import secure_filename
from render_soundgrid import make_grid
import math
import os

upload_folder = "./DataCubes"

app = Flask(__name__)
SECRET_KEY = os.urandom(32)
app.config["SECRET_KEY"] = SECRET_KEY


## Parameters input from webpage
class UploadForm(FlaskForm):
    file = FileField()
    minwl = FloatField("Minimum Wavelength ", validators=[Optional()])
    maxwl = FloatField("Maximum Wavelength", validators=[Optional()])
    minx = IntegerField("x Range", validators=[Optional()])
    maxx = IntegerField(":", validators=[Optional()])
    miny = IntegerField("y Range", validators=[Optional()])
    maxy = IntegerField(":", validators=[Optional()])


@app.route("/", methods=["GET", "POST"])
def index():
    form = UploadForm()
    minwl = form.data["minwl"]
    maxwl = form.data["maxwl"]
    minx = form.data["minx"] or 0
    maxx = form.data["maxx"] or 100
    miny = form.data["miny"] or 0
    maxy = form.data["maxy"] or 100
    errors = False
    has_data = os.path.isfile("static/pixcols.csv")
    if has_data:
        nspaxel = sum(1 for _ in open("static/pixcols.csv"))
    else:
        nspaxel = 1
    nside = math.isqrt(nspaxel)
    metadata = {"nside": nside, "ppix": 400 // nside}

    ## On 'Generate Audio Files':
    ## Check if new file selected, or previous filename has been saved
    fn = "./static/audio/filename.txt"
    if form.validate_on_submit():
        if not form.file.data:
            if not os.path.isfile(fn):
                flash("Please select a file to upload!", "error")
                errors = True
            else:
                with open(fn) as f:
                    fname = f.read()
        else:
            filename = secure_filename(form.file.data.filename)
            fname = os.path.join(upload_folder, filename)
            form.file.data.save(fname)
            with open(fn, "w") as f:
                f.write(fname)

        ## Check wavelength and spatial ranges (if selected) are valid
        if form.minwl.data and form.maxwl.data and minwl >= maxwl:
            flash("Selected wavelength range invalid!")
            errors = True
        if form.minx.data and form.maxx.data and minx >= maxx:
            flash("Selected spatial range invalid!")
            errors = True
        if form.miny.data and form.maxy.data and miny >= maxy:
            flash("Selected spatial range invalid!")
            errors = True

        ## write audio files, spectra and whitelight data
        if not errors:
            mgdata = make_grid(fname, minwl, maxwl, minx, maxx, miny, maxy)

        ## Pass filename to display below canvas
        if fname.find("/") != -1:
            fname = fname[fname.rindex("/") + 1 :]
        return redirect(url_for("index", form=form, metadata=metadata, fname=fname))

    ## Pass filename to display below canvas
    try:
        with open(fn) as f:
            fname = f.read()
            if fname.find("/") != -1:
                fname = fname[fname.rindex("/") + 1 :]
    except IOError:
        fname = ""
    return render_template(
        "index.html", form=form, metadata=metadata, fname=fname, has_data=has_data
    )


if __name__ == "__main__":
    app.run(debug=True, use_reloader=True)
