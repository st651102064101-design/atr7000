#!/bin/bash

RED='\033[0;31m'
NC='\033[0m'
old_rtls_path=""
this_clas_version=""
installer_version="1.3"

err() {
    printf '%s\n' "$1" >&2
}
die() {
    err "$1"
    exit 1
}

get_os() {
	if [ -f /etc/os-release ]; then
		. /etc/os-release
	elif [ -f /usr/lib/os-release ]; then
		. /usr/lib/os-release
	else
		die "Unable to detect OS"
	fi
}

approve() {
	prompt="${1}"

	while true; do
		read -p "${prompt} [y/N]: " yn
		case $yn in
			[Yy]* ) return 0;;
			* ) return 1;;
		esac
	done
}

centos_install_docker() {
    sudo yum install -y yum-utils
    sudo yum-config-manager \
        --add-repo \
        https://download.docker.com/linux/centos/docker-ce.repo

	if which podman 2>/dev/null; then
		if approve "This system already has podman. Podman's runtime is incompatible with Docker. Do you want to remove podman and install Docker?"; then
			sudo yum remove -y podman buildah
		else
			die "Podman is installed. Please remove podman and try again."
		fi
	fi

    sudo yum install -y docker-ce docker-ce-cli containerd.io
}

redhat_install_docker() {
    TRIMMED_VERSION=$(echo "${VERSION_ID}" | awk -F '.' '{ print $1 }')
    if [ "${TRIMMED_VERSION}" == "7" ]; then
        cat <<EOF | sudo tee /etc/yum.repos.d/CentOS-Extras.repo
[centos-extras]
name=Centos extras - $basearch
baseurl=http://mirror.centos.org/centos/7/extras/x86_64
enabled=1
gpgcheck=1
gpgkey=http://centos.org/keys/RPM-GPG-KEY-CentOS-7
EOF
    fi
    centos_install_docker

}

rocky_install_docker() {
    centos_install_docker

}

ubuntu_install_docker() {
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
        "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io
}

install_docker() {
	old_flags=$-
	set -e
    get_os

    case "${ID}" in
        "ubuntu")
            ubuntu_install_docker
            ;;
        "centos")
            centos_install_docker
            ;;
        "rhel")
            redhat_install_docker
            ;;
        "rocky")
            rocky_install_docker
            ;;
        *)
            die "Unsupported OS: ${ID}"
            ;;
    esac
    sudo systemctl enable --now docker

	set +e
	set -$old_flags
}

install_curl() {
    get_os

    case "${ID}" in
        "ubuntu")
            sudo apt-get update
            sudo apt-get install -y curl
            ;;
        "centos"|"rhel"|"rocky")
            sudo yum install -y curl
            ;;
        *)
            die "Unsupported OS: ${ID}"
            ;;
    esac
}

install_openssl() {
    get_os

    case "${ID}" in
        "ubuntu")
            sudo apt-get update
            sudo apt-get install -y openssl
            ;;
        "centos"|"rhel"|"rocky")
            sudo yum install -y openssl
            ;;
        *)
            die "Unsupported OS: ${ID}"
            ;;
    esac
}

install_python3() {
    get_os

    case "${ID}" in
        "ubuntu")
            sudo apt-get update
            sudo apt-get install -y python3
            ;;
        "centos"|"rhel"|"rocky")
            sudo yum install -y python3
            ;;
        *)
            die "Unsupported OS: ${ID}"
            ;;
    esac
}


function is_ntp_running
{
	display_msg "Cheking if NTP is running.."

	if timeout 3 nc -vzu localhost 123; then
		display_msg "NTP is running."
		true
	else
		display_msg "NTP is not running"
		false
	fi
}

function get_confirmation
{
	printf "${1} "
	read confirmation
#	echo "${confirmation}"
}

function log_error
{
	printf "${RED}${1}${NC}\n"
}

function log_warning
{
	printf "${RED}${1}${NC}\n"
}

function display_msg
{
	echo $1
}

function is_docker_compose_installed
{
	installed=`which docker-compose`

	if [ "$installed" == "" ];then
		false
	else
		true
	fi
}

function docker_compose_version
{
	docker_compose_version=`docker-compose --version | cut -d "," -f 1 | cut -d " " -f 3`
}

function install_docker_compose
{
	ret=`which curl`

	if [ "$ret" == "" ];then
		display_msg "curl not installed. Installing curl.."
		install_curl
	fi

	#sudo curl -kL "https://github.com/docker/compose/releases/download/1.24.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
	sudo curl -SL https://github.com/docker/compose/releases/download/v2.20.3/docker-compose-linux-x86_64 -o /usr/local/bin/docker-compose
	sudo chmod +x /usr/local/bin/docker-compose
	sudo ln -s /usr/local/bin/docker-compose /usr/bin/docker-compose
}

function is_docker_installed
{
	display_msg "looking for docker-ce installation"
	pkg=`which docker 2> /dev/null`
	if [ "$pkg" == "" ];then
		false
	else
		true
	fi
}

function is_wget_installed
{
	display_msg "looking for wget installation"
	pkg=`which wget 2>/dev/null`
	if [ "$pkg" == "" ];then
		false
	else
		true
	fi
}

function is_py3_installed
{
	display_msg "looking for python3 installation"
	pkg=`which python3 2>/dev/null`
	if [ "$pkg" == "" ];then
		false
	else
		true
	fi
}

function is_openssl_installed
{
	display_msg "looking for openssl installation"
	pkg=`which openssl 2>/dev/null`
	if [ "$pkg" == "" ];then
		false
	else
		true
	fi
}

function get_docker_version
{
	display_msg "checking docker version"
	#docker_ver=`dpkg -s docker-ce | grep -m 1 -i version | cut -d ":" -f 3 | cut -d "~" -f 1`
	docker_ver=`docker --version 2> /dev/null | sed -r 's|Docker version ([0-9]+)\.([0-9]+)\.([0-9]+).*|\1.\2.\3|'`
}

function is_rtls_running
{
	display_msg "Checking if older CLAS is running.."
	containers=`$SUDO docker ps | grep rtls`
	if [ "$containers" == "" ];then
		false
	else
		true
	fi
}

function is_rtls_installed
{
	display_msg "Checking for existing CLAS installations.."
	vol=`$SUDO docker volume ls | grep rtls_shared_vol`
	if [ "$vol" == "" ];then
		false
	else
		img=`$SUDO docker images | grep rtls_config`
		if [ "$img" == "" ];then
			false
		else
			true
		fi
	fi
}

function stop_rtls
{
	display_msg "Stopping old CLAS"
	cd $old_rtls_path
	./rtls.sh stop &> /dev/null
	cd -
}

function uninstall_rtls
{
	stop_rtls
	display_msg "Uninstalling existing CLAS.."
	cd $old_rtls_path
	compose_files=`ls docker-compose*.yml`
	cmd="$SUDO docker-compose "

	for file in $compose_files;
	do
		cmd="$cmd -f $file"
	done
	cmd="$cmd down --rmi all"
	$cmd
	cd -
}

function start_rtls
{
	display_msg "Starting old CLAS to migrate license."
	cd $old_rtls_path
	$SUDO ./rtls.sh start &> /dev/null &
	cd -
	display_msg "CLAS will take a few minutes to come up. please wait.."
	sleep 100
}


function validate_rtls_path
{
	if [ ! -f $old_rtls_path/.env ];then
		log_error "$old_rtls_path does not seem to be a valid CLAS installation"
		exit 1
	fi
}

function get_lic_params_from_old_installation
{
	ip=`cat $old_rtls_path/.env | grep RTLS_AAR_IFC_IP | cut -d "=" -f 2`
	mac=`ip addr show | grep -C 1 $ip | grep ether | cut -d "/" -f 2 | cut -d " " -f 2`
	hostid="CLAS_$(echo $mac | sed s/://g)"
	aid=`cat $old_rtls_path/config/rtls.conf | grep license_activation_id | cut -d "=" -f 2`
	reader_count=`cat $old_rtls_path/config/aar_info.csv | grep -v x | grep ATR7000 -c`
}

function get_host_id
{
	log_warning "********************************************************"
	log_warning "IMPORTANT: CLAS needs to know the details of the network interface on this host."
	log_warning "Please make sure that the selected network interface is on the same subnet as the ATR7000s."
	log_warning "********************************************************"
	interfaces=`ls -l /sys/class/net/ | grep -v virtual | grep devices | sed -r 's|.*\/([[:alpha:]])|\1|g'`
	ifc_count=`echo $interfaces | wc -w`
	menu=""
	if [ $ifc_count -gt 1 ];then
		for interface in $interfaces
		do
			this_ip=`ip -4 addr show $interface | grep inet | awk '{$1=$1};1' | cut -d " " -f 2 | cut -d "/" -f 1`
			menu="$menu\n$interface: $this_ip"
		done
		log_warning "Following network interfaces were found. Please specify the name of the network interface to use.\nThis network should be on the same subnet as the ATR7000s."
		log_warning "$menu"
		get_confirmation "Please enter the name of the network interface to use:"
		iface=$confirmation
		ip=`ip -4 addr show $iface | grep inet | grep -v secondary | head -n 1 | awk '{$1=$1};1' | cut -d " " -f 2 | cut -d "/" -f 1`
		mac=`ip addr show $iface | grep link/ether | awk '{$1=$1};1' | cut -d " " -f 2`
		hostid="CLAS_$(echo $mac | sed s/://g)"
	elif [ $ifc_count == 1 ];then
		ip=`ip -4 addr show $interfaces | grep inet | grep -v secondary | head -n 1 | awk '{$1=$1};1' | cut -d " " -f 2 | cut -d "/" -f 1`
		mac=`ip addr show $interfaces | grep link/ether | awk '{$1=$1};1' | cut -d " " -f 2`
		hostid="CLAS_$(echo $interfaces | sed s/://g)"
		log_warning "Found the following network interface: [$interfaces : $ip]"
		get_confirmation "Continue using this interface? [y/n]:"
		if [ "$confirmation" == "n" ];then
			get_confirmation "Please enter the name of the network interface to use:"
			iface=$confirmation
			ip=`ip -4 addr show $iface | grep inet | grep -v secondary | head -n 1 | awk '{$1=$1};1' | cut -d " " -f 2 | cut -d "/" -f 1`
			mac=`ip addr show $iface | grep link/ether | awk '{$1=$1};1' | cut -d " " -f 2`
			hostid="CLAS_$(echo $iface | sed s/://g)"
		fi
	else
		log_error "ERROR: No active network interface found."
		exit 1
	fi
}

function delete_certs
{
	is_openssl_installed
	if [ $? != 0 ];then
		install_openssl
	fi

	if sudo test -f "/var/lib/docker/volumes/rtls_shared_vol/_data/rtls_server_crt.pem";then
		issuer=`sudo openssl x509 -text -noout -in /var/lib/docker/volumes/rtls_shared_vol/_data/rtls_server_crt.pem | grep rtls@zebra.com`
		if [ "$issuer" != "" ];then
			display_msg "checking self signed certificate key length."
			length=`sudo openssl x509 -text -noout -in /var/lib/docker/volumes/rtls_shared_vol/_data/rtls_server_crt.pem |grep 'RSA Public-Key' | sed -r 's|.*RSA Public-Key: \(([0-9]+).*|\1|g'`
			display_msg "key length is ${length}"
			if [ "$length" == 1024 ];then
				log_warning "Deleting old certificates....."
				sudo rm /var/lib/docker/volumes/rtls_shared_vol/_data/rtls_server_crt.pem
				sudo rm /var/lib/docker/volumes/rtls_shared_vol/_data/rtls_server_key.pem
				sudo rm /var/lib/docker/volumes/rtls_shared_vol/_data/ca-certificates.crt
			fi
		fi
	fi
}

function revoke_old_license
{
	display_msg "returning licenses from readers.."
	python3 revoke_licenses.py
	if [ $? != 0 ];then
		log_error "Failed to return licenses. \nMake sure CLAS is running and all the readers are running and connected to internet."
		exit 1
	fi
}

function upgrade_2_2_26_or_older
{
	display_msg "upgrading CLAS.."
	is_py3_installed
	if [ $? != 0 ];then
		log_warning "Python3 not installed. Installing.."
		install_python3
	fi
}

function upgrade_2_2_27_or_newer
{
	echo "upgrading clas from $old_clas_version"
}

function upgrade_clas
{
	if [ $1 -le 2 ];then
		if [ $2 -le 2 ];then
			if [ $3 -le 26 ];then
				upgrade_2_2_26_or_older
				return $?
			fi
		fi
	fi
	upgrade_2_2_27_or_newer
}

function prompt_inst_path_and_validate
{
	while true ; do
		new_rtls_path="/opt/rtls"
		get_confirmation "CLAS will be installed in $new_rtls_path. Hit Enter to continue or specify a new path: "
		if [ "$confirmation" != "" ];then
			new_rtls_path=$confirmation
		fi
		new_rtls_path="$new_rtls_path/$this_clas_version"
		if [ -d $new_rtls_path ];then
			display_msg "Path already exists."
			files_in_new_path=`ls $new_rtls_path`
			if [ "$files_in_new_path" != "" ];then
				log_warning "New installation directory not empty. Please enter a different path."
				continue
			else
				break
			fi
		else
			display_msg "Creating directory for new installation.."
			sudo mkdir -p $new_rtls_path
			sudo chown -R $USER:$USER $new_rtls_path
			break
		fi
	done
}

function check_docker_and_install
{
	is_docker_installed
	if [ $? -eq 0 ];then
		get_docker_version
		docker_major_version="$(echo $docker_ver | sed -E 's|([[:digit:]]+)\.([[:digit:]]+)\.([[:digit:]]+)|\1|g')"
		docker_minor_version="$(echo $docker_ver | sed -E 's|([[:digit:]]+)\.([[:digit:]]+)\.([[:digit:]]+)|\2|g')"
		if [ $docker_major_version -lt 28 ];then
			display_msg "Docker version $docker_major_version.$docker_minor_version does not meet the prerequisites."
			log_error "Please update docker to atleast 28.5.1 and try again. "
			log_error "Go to the following link on installing the latest Docker."
			display_msg "https://docs.docker.com/engine/install/ubuntu/"
			#install_docker
			exit 1
		elif [ $docker_major_version -eq 28 ];then
			if [ $docker_minor_version -lt 5 ];then
				display_msg "Docker version $docker_major_version.$docker_minor_version does not meet the prerequisites."
				log_error "Please update docker to atleast 28.5.1and try again. "
				log_error "Go to the following link on installing the latest Docker."
				display_msg "https://docs.docker.com/engine/install/ubuntu/"
				#install_docker
				exit 1
			fi
		fi
		display_msg "Docker version $docker_major_version.$docker_minor_version is up to date."
	else
		log_warning "Docker not installed.Installing docker.."
		install_docker
	fi
	ingroups=`groups`
	for thisgroup in $ingroups
	do
		if [ "$thisgroup" == "docker" ];then
			requires_sudo=false
		fi
	done
	if $requires_sudo;then
		echo "User not in docker group...."
		SUDO=sudo
	fi
}

function check_compose_and_install
{
	is_docker_compose_installed
	if [ $? -eq 0 ]; then
		docker_compose_version
		docker_compose_major_version="$(echo $docker_compose_version | sed -E 's|([[:digit:]]+)\.([[:digit:]]+)\.([[:digit:]]+)|\1|g')"
		docker_compose_minor_version="$(echo $docker_compose_version | sed -E 's|([[:digit:]]+)\.([[:digit:]]+)\.([[:digit:]]+)|\2|g')"
		if [ "$docker_compose_major_version" -lt 2 ]; then
			display_msg "docker-compose version $docker_compose_major_version.$docker_compose_minor_version does not meet the prerequisites."
			display_msg "Updating docker-compose"
			install_docker_compose
		elif [ "$docker_compose_major_version" -eq 2 ]; then
			if [ "$docker_compose_minor_version" -lt 39 ]; then
				display_msg "docker-compose version $docker_compose_major_version.$docker_compose_minor_version does not meet the prerequisites."
				display_msg "Updating docker-compose."
				install_docker_compose
			fi
		fi
		display_msg "docker-compose version $docker_compose_major_version.$docker_compose_minor_version is up to date."
	else
		log_warning "docker-compose not installed.Installing docker-compose.."
		install_docker_compose
	fi
}

function import_config
{
	display_msg "Importing CLAS configuration."
	awk -F= '!a[$1]++' $old_rtls_path/.env $new_rtls_path/rtls/.env > $new_rtls_path/rtls/.env_merged
	cp $new_rtls_path/rtls/.env_merged $new_rtls_path/rtls/.env
	rm $new_rtls_path/rtls/.env_merged
	cp $old_rtls_path/config/aar_info.csv $new_rtls_path/rtls/config/aar_info.csv
	rest_port=`cat $old_rtls_path/.env | grep REST_PORT | cut -d "=" -f 2`
	ip=`cat $old_rtls_path/.env | grep RTLS_AAR_IFC_IP | cut -d "=" -f 2`

	if [ -f license.bin ]; then
		cp license.bin $new_rtls_path/rtls/config
	fi

	log_warning "CLAS has a bundled Kafka broker and will be configured to use that by default.\nIf you want to change this behaviour, select 'n' else select 'y'."
	get_confirmation "Use bundled Kafka broker? [y/n]: "
	if [ $confirmation == "n" ];then
		get_confirmation "Please enter the IP address of the Kafka broker in IP:PORT format and press Enter: "
		kafka_ip=$confirmation
		bundled_kafka=false
	else
		kafka_ip="$ip:9092"
		bundled_kafka=true
	fi

	sed -i "s|location_endpoint_addr.*|location_endpoint_addr = $kafka_ip|g" $new_rtls_path/rtls/config/rtls.conf
	sed -i "s|events_endpoint_addr.*|events_endpoint_addr = $kafka_ip|g" $new_rtls_path/rtls/config/rtls.conf
	sed -i "s|events_endpoint_type.*|events_endpoint_type = kafka|g" $new_rtls_path/rtls/config/rtls.conf
}


function configure_rtls
{
	display_msg "configuring CLAS installation."

	if [ -f license.bin ]; then
		cp license.bin $new_rtls_path/rtls/config
	fi

	log_warning "CLAS has a bundled Kafka broker and will be configured to use that by default.\nIf you want to change this behaviour, select 'n' else select 'y'."
	get_confirmation "Use bundled Kafka broker? [y/n]: "
	if [ $confirmation == "n" ];then
		get_confirmation "Please enter the IP address of the Kafka broker in IP:PORT format and press Enter: "
		kafka_ip=$confirmation
		bundled_kafka=false
	else
		kafka_ip="$ip:9092"
		bundled_kafka=true
	fi

	rest_port=`cat $new_rtls_path/rtls/.env | grep REST_PORT | cut -d "=" -f 2`
	sed -i "s|location_endpoint_addr.*|location_endpoint_addr = $kafka_ip|g" $new_rtls_path/rtls/config/rtls.conf
	sed -i "s|events_endpoint_addr.*|events_endpoint_addr = $kafka_ip|g" $new_rtls_path/rtls/config/rtls.conf
	sed -i "s|events_endpoint_type.*|events_endpoint_type = kafka|g" $new_rtls_path/rtls/config/rtls.conf
	sed -i "s|license_activation_id.*|license_activation_id = $aid|g" $new_rtls_path/rtls/config/rtls.conf
	sed -i "s|RTLS_AAR_IFC_IP.*|RTLS_AAR_IFC_IP=$ip|g" $new_rtls_path/rtls/.env
}

function install_rtls
{
	tar -xzf rtls_services_cr_$this_clas_version.tar.gz --directory $new_rtls_path
	if [ $? -ne 0 ]; then
		log_error "ERROR: extracting package to $new_rtls_path."
		exit 1
	fi

	if ! $fresh_install;then
		import_config
		delete_certs
	else
		get_host_id
		configure_rtls
	fi

	log_warning "Logging into docker registry. Please Enter your username and password."
	log_warning "The registry credentails are send to you in a email titled 'CLAS Beta' or 'CLAS software'."
	#get_confirmation "Enter username: "
	#registry_username=$confirmation
	#get_confirmation "Enter password: "
	#registry_password=$confirmation
	get_confirmation "Is this a production installation? [y/n] " 

	if [ "$confirmation" == "n" ];then
		docker_registry_address="dcs-clas-doc-ext.artifactory-apac.zebra.com"
		sed -i "s|DOCKER_REGISTRY_ADDRESS.*|DOCKER_REGISTRY_ADDRESS=$docker_registry_address|g" $new_rtls_path/rtls/.env
		$SUDO docker login $docker_registry_address
	else
		docker_registry_address=`cat $new_rtls_path/rtls/.env | grep DOCKER_REGISTRY_ADDRESS | cut -d  "=" -f 2`
		$SUDO docker login $docker_registry_address
	fi

	sudo mkdir -p /etc/rtls
	sudo chown -R $USER:$USER /etc/rtls
	sudo echo "ver=$this_clas_version" > /etc/rtls/meta.conf
	sudo echo "path=$new_rtls_path" >> /etc/rtls/meta.conf
}

function start_new_rtls
{
	rtls_options="d"
	if ! is_ntp_running; then
		rtls_options="${rtls_options}n"
	fi

	if $bundled_kafka; then
		display_msg "adding bundled kafka option."
		rtls_options="${rtls_options}k"
	fi

	get_confirmation "Start CLAS in sim mode? This will pull all the docker images from the registry and start CLAS. [y/n] "
	if [ "$confirmation" == "y" ];then
		cd $new_rtls_path/rtls
		$SUDO ./rtls.sh -s$rtls_options start
		display_msg "Successfully installed CLAS. Please go to the following URL in your browser."
		log_warning "\n\t\thttps://$ip:$rest_port"
		display_msg "To stop CLAS run 'cd $new_rtls_path/rtls && $SUDO ./rtls.sh -s$rtls_options stop'"
		display_msg "To start CLAS any time run  'cd $new_rtls_path/rtls && $SUDO ./rtls.sh -$rtls_options start'"
	else
		display_msg "Installer will now exit."
		display_msg "Next steps.."
		display_msg "1. Edit the $new_rtls_path/rtls/config/aar_info.csv file to add details of the ATR7000"
	        display_msg "2. start CLAS using 'cd $new_rtls_path/rtls && $SUDO ./rtls.sh -$rtls_options start"
	fi
}

function start_upgrade
{
	log_warning "This script will install/upgrade the CLAS software on this host."
	get_confirmation "Continue? [y/n]"

	if [ "$confirmation" == "n" ];then
		echo "aborting CLAS install."
		exit 0
	fi

	display_msg "Installing CLAS.."
	check_docker_and_install
	check_compose_and_install

	fresh_install=true

	is_rtls_installed
	if [ $? -eq 0 ];then
		log_warning "Detected previous CLAS installation."
		get_confirmation "Uninstall the previous version of CLAS? [y/n] "
		if [ "$confirmation" == "y" ]; then
			fresh_install=false
			if [ -f "/etc/rtls/meta.conf" ]; then
				old_rtls_path=`cat /etc/rtls/meta.conf | grep path | cut -d "=" -f 2`
				old_rtls_path="$old_rtls_path/rtls"
			else
				printf "Please enter the path where CLAS is currently installed: "
				read old_rtls_path
			fi
			validate_rtls_path
			uninstall_rtls
		else
			log_error "Cancelling installation"
			return 1
		fi
	fi

	prompt_inst_path_and_validate
	install_rtls
	start_new_rtls
}
if [ "$1" == "version" ];then
	echo $installer_version
	exit 0
fi

this_clas_version=`ls rtls_services_cr*.tar.gz | sed -r 's|.*([0-9]+)\.([0-9]+)\.([0-9]+)\.([0-9]+).*|\1.\2.\3.\4|g'`
start_upgrade
