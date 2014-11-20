from collections import namedtuple

from rosdistro import get_distribution_file
from rosdistro import get_index
from rosdistro import get_release_build_files

from ros_buildfarm.jenkins import connect
from ros_buildfarm.jenkins import invoke_job

from .common import get_debian_package_name
from .debian_repo import get_debian_repo_data
from .release_job import get_binarydeb_job_name
from .release_job import get_sourcedeb_job_name
from .status_page import _strip_version_suffix


def trigger_release_jobs(
        rosdistro_index_url, rosdistro_name, release_build_name,
        missing_only, source_only, cache_dir):
    index = get_index(rosdistro_index_url)
    build_files = get_release_build_files(index, rosdistro_name)
    build_file = build_files[release_build_name]

    # get targets
    Target = namedtuple('Target', 'os_name os_code_name arch')
    targets = []
    for os_name in sorted(build_file.get_target_os_names()):
        if os_name != 'ubuntu':
            continue
        for os_code_name in sorted(
                build_file.get_target_os_code_names(os_name)):
            targets.append(Target('ubuntu', os_code_name, 'source'))
            if source_only:
                continue
            for arch in sorted(
                    build_file.get_target_arches(os_name, os_code_name)):
                # TODO support for non amd64 arch missing
                if arch not in ['amd64']:
                    print('Skipping arch:', arch)
                    continue
                targets.append(Target('ubuntu', os_code_name, arch))
    print('The build file contains the following targets:')
    for os_name, os_code_name, arch in targets:
        print('  - %s %s %s' % ('ubuntu', os_code_name, arch))

    dist_file = get_distribution_file(index, rosdistro_name)

    conf = build_file.get_target_configuration()
    assert 'apt_target_repository' in conf
    repo_url = conf['apt_target_repository']

    repo_data = None
    if missing_only:
        repo_data = get_debian_repo_data(repo_url, targets, cache_dir)

    jenkins = connect(build_file.jenkins_url)

    pkg_names = dist_file.release_packages.keys()
    pkg_names = build_file.filter_packages(pkg_names)

    triggered_jobs = []
    skipped_jobs = []
    for pkg_name in sorted(pkg_names):
        pkg = dist_file.release_packages[pkg_name]
        repo_name = pkg.repository_name
        repo = dist_file.repositories[repo_name]
        if not repo.release_repository:
            print(("  Skipping package '%s' in repository '%s': no release " +
                   "section") % (pkg_name, repo_name))
            continue
        if not repo.release_repository.version:
            print(("  Skipping package '%s' in repository '%s': no release " +
                   "version") % (pkg_name, repo_name))
            continue
        pkg_version = repo.release_repository.version

        debian_package_name = get_debian_package_name(rosdistro_name, pkg_name)

        for target in targets:
            job_name = get_sourcedeb_job_name(
                rosdistro_name, release_build_name,
                pkg_name, target.os_name, target.os_code_name)
            if target.arch != 'source':
                # binary job can be skipped if source job was triggered
                if job_name in triggered_jobs:
                    print(("  Skipping binary jobs of '%s' since the source " +
                           "job was triggered") % job_name)
                    continue
                job_name = get_binarydeb_job_name(
                    rosdistro_name, release_build_name,
                    pkg_name, target.os_name, target.os_code_name, target.arch)

            if repo_data:
                # check if artifact is missing
                repo_index = repo_data[target]
                if debian_package_name in repo_index:
                    version = repo_index[debian_package_name]
                    version = _strip_version_suffix(version)
                    if version == pkg_version:
                        print(("  Skipping job '%s' since the artifact is " +
                               "already up-to-date") % job_name)
                        continue

            success = invoke_job(jenkins, job_name)
            if success:
                triggered_jobs.append(job_name)
            else:
                skipped_jobs.append(job_name)

    print('Triggered %d jobs, skipped %d jobs.' %
          (len(triggered_jobs), len(skipped_jobs)))