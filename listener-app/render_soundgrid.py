# strauss imports
import glob
import time

import astropy.io.fits as pyfits
import matplotlib.pyplot as plt
import numpy as np
import wavio as wav

# other useful modules
from matplotlib import cm
from PIL import Image, ImageOps
from scipy.io import wavfile
from strauss.generator import Spectralizer
from strauss.score import Score
from strauss.sonification import Sonification
from strauss.sources import Objects


def get_continuum(wavelengths, spectrum, deg=12):
    # we fit the continuum of the spectrum as
    # p[0]*wavelengths**deg + p[1]*wavelengths**(deg-1) + ... + p[deg]*wavelengths**0
    # using fancy array operations...
    powers = np.arange(deg + 1)[::-1]
    p = np.polyfit(wavelengths, spectrum, deg=deg)
    return (
        np.column_stack([p] * wavelengths.size)
        * pow(wavelengths, np.column_stack([powers] * wavelengths.size))
    ).sum(axis=0)


def dsamp_ifu(a, dsamp):
    a = a.copy().T
    preshape = a.shape[:-1]
    remx = preshape[0] % dsamp
    remy = preshape[1] % dsamp
    augx = (dsamp - remx) % dsamp
    augy = (dsamp - remy) % dsamp

    host = np.zeros((preshape[0] + augx, preshape[1] + augy, a.shape[-1]))
    offstx = augx // 2
    offsty = augy // 2
    # print(remx,remy, host.shape, offstx, host.shape[0]-augx+offstx,  offsty, host.shape[1]-augy+offsty)
    host[
        offstx : host.shape[0] - augx + offstx,
        offsty : host.shape[1] - augy + offsty,
        :,
    ] = a
    shape = (host.shape[0] // dsamp, host.shape[1] // dsamp)
    sh = (
        shape[0],
        host.shape[0] // shape[0],
        shape[1],
        host.shape[1] // shape[1],
        host.shape[-1],
    )
    return host.reshape(sh).mean(-2).mean(1).T


def make_whitelight(
    data,
    pc=99.0,
    contrast=0.5,
    outfile="static/images/whitelight.png",
    # greyshade=0.0,
    bordpix=5,
    sidelen=390,
    padding=0,
):
    wlight = data.sum(axis=0)
    wlight /= np.percentile(wlight, pc)
    # clip to percentile limit and apply contrast
    wlight = np.clip(wlight, 0, 1) ** contrast
    cmap = (cm.magma(wlight[::-1, ::-1].T) * 255).astype("uint8")
    # greyval = int(greyshade * 255)

    wdims = np.array(cmap.shape[:-1])
    # inset_x = inset_y = 0

    # nmax = wdims.max()
    sizefac = sidelen / wdims.max()
    newsize = np.round(np.array(wdims)[::-1] * sizefac / 2).astype(int) * 2
    pixdiff = np.max(newsize) - newsize
    pixmarg = pixdiff // 2 + padding
    print(newsize, tuple(pixdiff), cmap.shape)
    img = Image.fromarray(cmap, "RGBA").resize(tuple(newsize), Image.Resampling.NEAREST)
    img = ImageOps.expand(img, border=tuple(pixmarg), fill="black")
    img = ImageOps.expand(img, border=bordpix, fill="white")
    img.save(outfile)
    return pixmarg

    # if wdims[0] != wdims[1]:
    #     # pad to square
    #     wdimord = np.argsort(wdims)
    #     hostarr = np.dstack([np.zeros([wdims[wdimord[1]]]*2)]*4)
    #     hostarr[:,:,:3] = greyval
    #     hostarr[:,:,-1] = 255
    #     print(hostarr.shape, cmap.shape)
    #     margin = (wdims[wdimord[1]] - wdims[wdimord[0]]) // 2
    #     # indexing depends on which axis is bigger
    #     if np.diff(wdimord)[0]:
    #         hostarr[margin:wdims[0]+margin,:,:] = cmap
    #         inset_x += 2*margin
    #     else:
    #         hostarr[:,margin:wdims[1]+margin,:] = cmap
    #         inset_y += 2*margin
    #     cmap = hostarr.astype('uint8')
    #     print(hostarr[:,:,0])

    # print((sidelen) / np.abs(np.diff(wdims)), padding)

    # if padding:
    #     final = np.zeros((cmap.shape[0]+2*padding, cmap.shape[1]+2*padding,
    #                       cmap.shape[2])).astype('uint8')+greyval
    #     final[:,:,-1] = 255
    #     final[padding:-padding,padding:-padding,:] = cmap
    # else:
    #     final = cmap

    # # plt.imsave(outfile, wlight[::-1,::-1].T, cmap='magma')
    # img = Image.fromarray(final, 'RGBA').resize((sidelen,sidelen), Image.Resampling.NEAREST)
    # img = ImageOps.expand(img,border=bordpix,fill='white')
    # # img.save(outfile)


def make_grid(fname, minwl=None, maxwl=None, minx=0, maxx=24, miny=0, maxy=24):
    """Reads datacube. Prepares and writes data for spectra and whitelight
    image into csv files. Creates audio files for each pixel in image."""

    mgdata = {}

    maxn = 800
    minspaxside = 4

    # move globals here (L20-34)

    acontrast = 1.0 * 1.4
    vcontrast = 0.5

    # specify audio system (e.g. mono, stereo, 5.1, ...)
    system = "mono"
    length = 1.1

    # set up score object for sonification
    score = Score(["C3"], length)
    generator = Spectralizer()
    generator.modify_preset({"min_freq": 40, "max_freq": 800})

    # viewfinder properties
    totlen = 400  # pixels
    bordpix = 5  # pixels
    fieldsize = totlen - 2 * bordpix

    if not glob.glob(fname):
        print("No such file!")
        return

    for f in glob.glob(fname):
        t0 = time.time()
        data, header = pyfits.getdata(f, header=True)

        if "CDELT3" not in header:
            if "CD3_3" in header:
                header["CDELT3"] = header["CD3_3"]

        data[np.isnan(data)] = 0.0
        wlens = np.linspace(
            header["CRVAL3"],
            (header["NAXIS3"] - 1) * header["CDELT3"] + header["CRVAL3"],
            header["NAXIS3"],
        )

        ## Set wavelength range to cover whole spectrum
        lidx = np.argmin(wlens)
        ridx = np.argmax(wlens)

        ## Reduce wavelength range if valid options selected
        if minwl is not None:
            if minwl > np.min(wlens) and minwl < np.max(wlens):
                lidx = np.where(wlens >= minwl)[0][0]
        if maxwl is not None:
            if maxwl < np.max(wlens) and maxwl > np.min(wlens):
                ridx = np.where(wlens >= maxwl)[0][0]

        data = data[lidx:ridx]
        wlens = wlens[lidx:ridx]

        pixmarg = make_whitelight(
            data, contrast=vcontrast, bordpix=bordpix, sidelen=fieldsize
        )
        print(pixmarg)
        print(data.shape, header["CDELT3"], header["CRVAL3"])
        # plt.plot(data.sum(axis=-1).sum(axis=-1))
        # plt.show()
        # data = dsamp_ifu(data,3)

        # maxpos = np.unravel_index(np.argmax(data.sum(0)), data.shape[1:])

        # r_cen = np.asarray(maxpos[::-1])
        # r_cen = (data.shape[1]//2, data.shape[2]//2)
        # print(r_cen)

        ## Re-centre on selected spatial selection
        # offset_x = int((maxx - minx) / 2) - 12
        # offset_y = int((maxy - miny) / 2) - 12
        # r_cen[1] += offset_x
        # r_cen[0] += offset_y

        # ap = 12
        # censel = data[:,r_cen[1]-ap:r_cen[1]+ap,r_cen[0]-ap:r_cen[0]+ap]

        pcmarg = pixmarg / fieldsize
        print(pcmarg * 100)
        print(maxx, minx, maxy, miny)

        x1 = int(
            np.round(
                data.shape[1] * (((100 - maxx) - pcmarg[1]) / (100.0 - 2 * pcmarg[1]))
            )
        )
        x2 = int(
            np.round(
                data.shape[1] * (((100 - minx) - pcmarg[1]) / (100.0 - 2 * pcmarg[1]))
            )
        )
        y1 = int(
            np.round(
                data.shape[2] * (((100 - maxy) - pcmarg[0]) / (100.0 - 2 * pcmarg[0]))
            )
        )
        y2 = int(
            np.round(
                data.shape[2] * (((100 - miny) - pcmarg[0]) / (100.0 - 2 * pcmarg[0]))
            )
        )

        # clip to index range
        x1 = np.clip(x1, 0, data.shape[1])
        y1 = np.clip(y1, 0, data.shape[2])
        x2 = np.clip(x2, 0, data.shape[1])
        y2 = np.clip(y2, 0, data.shape[2])

        xdiff = x2 - x1
        ydiff = y2 - y1
        delta = min(max(max(xdiff, ydiff), minspaxside), min(data.shape[1:]))
        print(x1, x2, y1, y2, xdiff, ydiff, delta)

        x1 += int(np.clip(data.shape[1] - x1 - delta, -np.inf, 0))
        y1 += int(np.clip(data.shape[2] - y1 - delta, -np.inf, 0))
        x2 = int(x1 + delta)
        y2 = int(y1 + delta)

        print(x1, x2, y1, y2)
        # x1 = ap - minx
        # x2 = maxx - ap
        # y1 = ap - miny
        # y2 = maxy - ap
        # censel = data[:,r_cen[1]-x1:r_cen[1]+x2,r_cen[0]-y1:r_cen[0]+y2]
        # exit()
        censel = data[:, x1:x2, y1:y2]
        numpix = (x2 - x1) * (y2 - y1)
        dfac = -int(-np.sqrt(numpix) // np.sqrt(maxn))

        censel = dsamp_ifu(censel, dfac)
        # censel = data[:, 3:-3, :]
        # print(censel)
        censel = np.rot90(censel, k=3, axes=(1, 2))
        # print(censel)
        # plt.imshow(np.clip(censel.sum(0), 0, np.percentile(censel.sum(0), 99))**vcontrast)
        vols = np.clip(censel.sum(0), 0, np.percentile(censel.sum(0), 99)) ** vcontrast
        vols[np.isnan(vols)] = 0
        vols /= vols.max()
        # print(vols)
        # plt.show()
        nsamp = int(48000 * 0.1 // 1)
        fade = np.linspace(0.0, 1.0, nsamp)

        t1 = time.time()

        print(f"setup {t1 - t0:.2f} s")

        pixcol = []
        plt.figure(figsize=(16, 16))

        spec = np.zeros((wlens.size, censel.shape[1] * censel.shape[2]))
        print(spec.shape, censel.shape)

        for i in range(censel.shape[1]):
            for j in range(censel.shape[2]):
                # plt.subplot2grid((ap*2,ap*2), (i,j))
                # plt.plot(wlens,censel[:,i,j])
                # plt.axis('off')
                # cont_avg = get_continuum(wlens, censel[:, i, j], 2)
                consub = censel[:, i, j] - np.percentile(censel[:, i, j], 50)
                consub[consub < consub.max() * 0.18] = 0.0  # 0.15
                # consub[consub < 0] = 0.
                pixcol.append(plt.cm.magma(vols[i, j])[:-1])
                pars = {"spectrum": [consub[::-1] ** acontrast], "pitch": [1]}
                # maxval = np.max(pars["spectrum"][0])
                # pars = {'spectrum':[(consub[::-1] == consub[::-1].max()).astype(float)], 'pitch':[1]}
                spec[:, i * censel.shape[2] + j] = pars["spectrum"][0][::-1]
                # if not maxval:
                #     continue
                sources = Objects(pars.keys())
                sources.fromdict(pars)
                sources.apply_mapping_functions()
                soni = Sonification(score, sources, generator, system)

                soni.render()
                # print(vols[i,j])
                soni.save(
                    f"static/audio/snd_{i}_{j}.wav", master_volume=vols[i, j] ** 1.3
                )

                # plt.plot(wlens, consub)
                wave = wav.read(f"static/audio/snd_{i}_{j}.wav")
                if i == 150 and j == 150:
                    plt.close()
                    # print(list(pars['spectrum'][0]))
                    plt.plot(pars["spectrum"][0])
                    plt.show()
                cwave = np.array(wave.data[:-nsamp], dtype="float64")[:, 0]
                cwave[:nsamp] *= fade
                cwave[:nsamp] += (
                    np.array(wave.data[-nsamp:], dtype="float64")[:, 0] * fade[::-1]
                )
                # plt.plot(cwave[:nsamp])
                # plt.plot(cwave[::-1][:nsamp])
                # plt.show()
                wavfile.write(
                    f"static/audio/snd_{i}_{j}.wav", 48000, cwave.astype("int32")
                )
                # plt.semilogy()
        t2 = time.time()
        print(f"setup {t2 - t1:.2f} s")

        intspec = spec.sum(axis=-1)
        intspec = np.clip((intspec / intspec.max()), 5e-4, 1)

        outspec = np.clip((spec.T / spec.max()), 5e-4, 1)

        np.savetxt(
            "static/pixcols.csv",
            (np.vstack(pixcol) * 255).astype(int),
            delimiter=",",
            fmt="%d",
        )
        np.savetxt("static/wlens.csv", wlens, delimiter=",", fmt="%e")
        np.savetxt(
            "static/spec.csv", np.vstack([wlens, outspec]), delimiter=",", fmt="%e"
        )
        np.savetxt(
            "static/intspec.csv",
            np.vstack([wlens, intspec]),
            delimiter=",",
            fmt="%e",
        )

        # print(wlens, intspec)

        # plt.show()

        # plt.title(f'Average Spectrum for Channel {ch} Data Cube')
        # plt.plot(wlens,data.mean(-1).mean(-1))

        # plt.xlabel(header['CUNIT3'])
        return mgdata


if __name__ == "__main__":
    # which channel do we want to use (1,2,3 or 4)?
    ch = 2
    fname = f"DataCubes/NGC7319_nucleus_MIRI_MRS/JWST/*ch{ch}*/*.fits"
    # fname = f'DataCubes/J1316+1753_240222.fits'
    # fname = f'DataCubes/Level3_ch1-long_s3d.fits'
    # fname = "./DataCubes/NGC7319_nucleus_MIRI_MRS/JWST/jw02732-c1001_t004_miri_ch1-shortmediumlong-/jw02732-c1001_t004_miri_ch1-shortmediumlong-_s3d.fits"
    make_grid(fname, minwl=None, maxwl=None, minx=0, maxx=24, miny=0, maxy=24)
